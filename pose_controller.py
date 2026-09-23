"""
pose_controller.py
────────────────────
SERVER-SIDE pose-mimic controller. Runs inside the SAME process as
main.py / orchestrator.py / robot_worker.py (the AlphaMini SDK process).

IMPORTANT — this file must stay free of cv2 / mediapipe imports.

Why: mediapipe requires protobuf>=4.25.3,<5. The AlphaMini SDK needs
protobuf==3.20.3 (newer protobuf breaks its generated message classes with
"TypeError: Descriptors cannot be created directly"). Those two requirements
cannot both be satisfied in one Python environment. So the webcam + MediaPipe
work lives in the separate script `pose_client.py`, which runs in its OWN
Python environment (see README) and talks to this server over plain local
HTTP (POST /pose/relay) — never importing the Mini SDK, never touching the
robot directly. There is still only ONE AlphaMini SDK connection in the
whole system: it lives entirely in this process, owned by `Orchestrator`.

This module owns:
    - the pose -> action name mapping (edit the block below)
    - rate limiting (max command_rate_hz) and same-pose-repeat suppression
    - Web-UI-priority gating (via orchestrator.is_phase_running()/is_busy())
    - a watchdog thread that sends one neutral command if pose updates stop
      arriving for tracking_loss_timeout_seconds while mimicking is ON —
      this covers BOTH "no person in frame" and "vision client crashed/
      closed", since either way updates simply stop arriving here.

Flask (webserver.py) calls into this module from:
    GET  /pose/status   -> get_status()
    POST /pose/start    -> enable()
    POST /pose/stop     -> disable()
    POST /pose/relay    -> report_pose(pose_label) / force_neutral()
"""

import threading
import time
from dataclasses import dataclass


# ═════════════════════════════════════════════════════════════════════════════
# EDIT THESE TO MATCH YOUR ALPHAMINI ACTION NAMES
#
# If a custom action name below does not exist on your robot's firmware,
# the worker will just print an execution error for that command — it will
# not crash the program. Swap in whatever names you've confirmed work
# (e.g. "009" is a built-in "stand/idle" action that is very likely to
# exist, so it's used as the neutral fallback here).
#
# Each of these is now a LITERAL match to what the camera detects: waving
# at the camera triggers the Wave action, bowing triggers the Bow action,
# raising both arms triggers the Arms Up action. No more "detect X, play Y"
# proxy mapping.
# ═════════════════════════════════════════════════════════════════════════════
ACTION_NEUTRAL   = "009"                # built-in stand/idle
ACTION_ARMS_UP   = "arms_up"
ACTION_WAVE      = "Surveillance_003"    # Wave, from actions.py
ACTION_BOW       = "bow_avatar"          # Bow, from actions.py — robot says "Thank you very much!"
# ═════════════════════════════════════════════════════════════════════════════

# Pose labels — MUST match the strings sent by pose_client.py exactly.
NO_PERSON  = "NO_PERSON"
UNKNOWN    = "UNKNOWN"
NEUTRAL    = "Neutral"
ARMS_UP    = "Arms Up"
WAVE       = "Wave"
BOW        = "Bow"

ACTIONABLE_POSES = (NEUTRAL, ARMS_UP, WAVE, BOW)


@dataclass
class Config:
    # Command update limits.
    command_rate_hz: float = 5.0
    same_pose_repeat_seconds: float = 2.0
    tracking_loss_timeout_seconds: float = 0.75

    # Robot named actions — see the EDIT block above.
    action_neutral: str = ACTION_NEUTRAL
    action_arms_up: str = ACTION_ARMS_UP
    action_wave: str = ACTION_WAVE
    action_bow: str = ACTION_BOW

    # How long (seconds) to mark the target robot "busy" after a pose command
    # is sent, purely to serialize sends with the Web UI. Keep small — pose
    # commands are frequent and shouldn't lock out manual control for long.
    busy_seconds_after_pose: float = 0.15


class SystemState:
    IDLE = "IDLE"
    MIRRORING = "MIRRORING"


class PoseMimicController:
    """
    Server-side gate between the vision client (pose_client.py) and the
    Orchestrator. No camera, no MediaPipe — just bookkeeping + safety.
    """

    def __init__(self, orchestrator, shutdown_event, get_targets, config=None, demo_mode=False):
        self.orchestrator = orchestrator
        self.shutdown_event = shutdown_event
        self.get_targets = get_targets
        self.config = config or Config()
        self.demo_mode = demo_mode

        self.pose_action_map = {
            NEUTRAL: self.config.action_neutral,
            ARMS_UP: self.config.action_arms_up,
            WAVE: self.config.action_wave,
            BOW: self.config.action_bow,
        }

        self._state_lock = threading.Lock()
        self.pose_enabled = False
        self.current_pose = NO_PERSON
        self.system_state = SystemState.IDLE

        self.last_pose_sent = None
        self.last_command_time = 0.0
        self.last_neutral_sent_time = 0.0
        self.last_tracking_time = time.time()
        self._loss_neutral_sent = False
        self._last_neutral_transition_time = 0.0

        self._watchdog_running = True
        threading.Thread(target=self._watchdog_loop, daemon=True, name="PoseWatchdog").start()

    # ─── external controls (thread-safe; called from Flask routes) ─────────

    def enable(self):
        with self._state_lock:
            self.pose_enabled = True
            self.system_state = SystemState.MIRRORING
            self.last_tracking_time = time.time()   # give the client a fresh grace window
            self._loss_neutral_sent = False
        print("  [pose] mirroring ENABLED")

    def disable(self, send_neutral=True):
        with self._state_lock:
            self.pose_enabled = False
            self.system_state = SystemState.IDLE
        if send_neutral:
            self._send_neutral(force=True)
        print("  [pose] mirroring DISABLED")

    def toggle(self):
        if self.pose_enabled:
            self.disable()
        else:
            self.enable()

    def get_status(self):
        with self._state_lock:
            return {
                "available": True,
                "enabled": self.pose_enabled,
                "state": self.system_state,
                "pose": self.current_pose,
                "demo_mode": self.demo_mode,
            }

    def stop(self):
        """Called on full app shutdown — stops the watchdog thread."""
        self._watchdog_running = False

    # ─── called by the /pose/relay Flask route ──────────────────────────────

    def report_pose(self, pose_label):
        """
        A vision client reported a detected pose this frame.

        Neutral is treated as a RESTING state, not an active pose to mimic:
        the stand/neutral action fires once on the transition into Neutral
        (or on the very first frame after enabling), then goes silent while
        the person stays in Neutral — otherwise the robot re-plays the
        stand action every couple of seconds and looks like it's fidgeting
        non-stop instead of holding still.
        """
        if pose_label not in (NO_PERSON, UNKNOWN) + ACTIONABLE_POSES:
            pose_label = UNKNOWN

        now = time.time()
        with self._state_lock:
            self.current_pose = pose_label
            mimicking = self.pose_enabled

        if pose_label != NO_PERSON:
            self.last_tracking_time = now
            self._loss_neutral_sent = False

        action_sent = ""
        note = ""
        if mimicking:
            if pose_label == NO_PERSON:
                note = "waiting_for_watchdog"
            elif self._blocked_by_web_ui():
                note = "skipped_webui_priority"
            elif pose_label == NEUTRAL:
                action_sent = self._send_neutral_on_transition()
                if not action_sent:
                    note = "already_neutral_holding_still"
            elif pose_label in (ARMS_UP, WAVE, BOW):
                action_sent = self._send_pose_action(pose_label)

        return {"ok": True, "enabled": mimicking, "action_sent": action_sent, "note": note}

    def force_neutral(self):
        """A vision client pressed 'n' — send neutral immediately."""
        sent = self._send_neutral(force=True)
        return {"ok": True, "sent": sent}

    # ─── internal send helpers ───────────────────────────────────────────────

    def _targets(self):
        try:
            return list(self.get_targets() or [])
        except Exception:
            return []

    def _send_action(self, action_name):
        if not action_name:
            return False
        if self.demo_mode:
            print("  [pose:DEMO] would send action '{}'".format(action_name))
            return True

        targets = self._targets()
        if not targets:
            return False

        cmd = {"type": "action", "name": action_name, "wait": False}
        sent_any = False
        for h in targets:
            if self.orchestrator.try_send(h, cmd, busy_seconds=self.config.busy_seconds_after_pose):
                sent_any = True
        return sent_any

    def _send_neutral(self, force=False):
        """
        Neutral is the safety fallback (tracking-loss watchdog, 'n' key, mimic
        OFF). It deliberately bypasses the short post-pose busy reservation
        used by _send_action() — a safety neutral must not get silently
        dropped just because it lands inside another pose command's 0.15s
        cooldown window. It still respects a running Web UI phase, so it
        won't stomp on a choreographed dance/march/wave sequence.
        """
        now = time.time()
        if not force and (now - self.last_neutral_sent_time) < self.config.same_pose_repeat_seconds:
            return False
        self.last_neutral_sent_time = now

        action_name = self.config.action_neutral
        if not action_name:
            return False
        if self.demo_mode:
            print("  [pose:DEMO] would send neutral action '{}'".format(action_name))
            return True

        targets = self._targets()
        if not targets or self.orchestrator.is_phase_running():
            return False

        cmd = {"type": "action", "name": action_name, "wait": False}
        for h in targets:
            self.orchestrator.send(h, cmd)   # direct send — bypasses busy cooldown on purpose
        return True

    def _send_neutral_on_transition(self):
        """
        Fires the neutral/stand action exactly once per transition INTO
        Neutral (from a different pose, or from the very first frame after
        enabling) — never on every frame while the person just stands there.
        A short local cooldown guards against classifier flicker re-firing
        this on rapid Neutral/Unknown noise.
        """
        if self.last_pose_sent == NEUTRAL:
            return ""

        now = time.time()
        if now - self._last_neutral_transition_time < 1.0:
            return ""

        if self._send_neutral(force=True):
            self.last_pose_sent = NEUTRAL
            self._last_neutral_transition_time = now
            return self.config.action_neutral
        return ""

    def _blocked_by_web_ui(self):
        if self.orchestrator.is_phase_running():
            return True
        targets = self._targets()
        if not targets:
            return False
        return all(self.orchestrator.is_busy(h) for h in targets)

    def _should_send_pose(self, pose_label):
        action = self.pose_action_map.get(pose_label)
        if not action:
            return False
        now = time.time()
        min_interval = 1.0 / self.config.command_rate_hz
        if now - self.last_command_time < min_interval:
            return False
        if pose_label == self.last_pose_sent and now - self.last_command_time < self.config.same_pose_repeat_seconds:
            return False
        return True

    def _send_pose_action(self, pose_label):
        action = self.pose_action_map.get(pose_label)
        if not action or not self._should_send_pose(pose_label):
            return ""
        if self._send_action(action):
            self.last_pose_sent = pose_label
            self.last_command_time = time.time()
            return action
        return ""

    # ─── watchdog: fail-safe if updates stop arriving ───────────────────────

    def _watchdog_loop(self):
        while self._watchdog_running and not self.shutdown_event.is_set():
            time.sleep(0.15)
            with self._state_lock:
                enabled = self.pose_enabled
            if not enabled:
                continue
            if (time.time() - self.last_tracking_time) > self.config.tracking_loss_timeout_seconds:
                if not self._loss_neutral_sent:
                    self._send_neutral(force=True)
                    self._loss_neutral_sent = True
                    self.last_pose_sent = NEUTRAL   # robot is now resting — avoid a redundant re-send later
                    with self._state_lock:
                        self.current_pose = NO_PERSON
                    print("  [pose] no updates from vision client — sent neutral (tracking loss / client offline)")
