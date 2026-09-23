#!/usr/bin/env python3
"""
pose_client.py
────────────────
Standalone webcam + MediaPipe pose-detection client for the AlphaMini
Campus Club Fair project.

WHY THIS IS A SEPARATE SCRIPT / VENV FROM main.py:
    mediapipe requires protobuf>=4.25.3,<5. The AlphaMini SDK (used by
    main.py / robot_worker.py / orchestrator.py) needs protobuf==3.20.3 —
    installing a newer protobuf breaks the SDK's generated message classes
    with "TypeError: Descriptors cannot be created directly", while
    installing the older protobuf the SDK needs breaks mediapipe with
    "RuntimeError: ValidatedGraphConfig Initialization failed". These two
    requirements cannot both be satisfied in one Python environment.

    So pose detection runs HERE, in its own venv (see README —
    requirements-vision.txt), and talks to the Flask server started by
    main.py over plain local HTTP. This script never imports the Mini SDK
    and never touches the robot directly — there is still only ONE
    AlphaMini SDK connection in the whole system, owned entirely by
    main.py's process.

Run (in a SECOND terminal, using the SECOND venv — see README):
    python pose_client.py --server http://127.0.0.1:5050

Test without a server at all:
    python pose_client.py --demo

Keyboard controls (focus must be on the OpenCV window):
    s = start/stop pose mirroring (kept in sync with the phone Web UI toggle)
    n = send neutral action immediately
    q = close this window (server + robot + web UI keep running)
"""

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import cv2
import mediapipe as mp
import numpy as np


# Pose labels — MUST match pose_controller.py on the server exactly.
NO_PERSON       = "NO_PERSON"
UNKNOWN         = "UNKNOWN"
NEUTRAL         = "Neutral"
ARMS_UP         = "Arms Up"
WAVE            = "Wave"
BOW             = "Bow"

WINDOW_NAME = "AlphaMini MimicMini - Pose Mirror"


# -----------------------------
# Configuration
# -----------------------------

@dataclass
class Config:
    camera_index: int = 0
    min_detection_confidence: float = 0.60
    min_tracking_confidence: float = 0.60
    landmark_visibility_threshold: float = 0.50

    wrist_above_shoulder_margin: float = 0.05
    wrist_near_shoulder_margin: float = 0.10
    elbow_straight_min_deg: float = 135.0   # arm extended straight -> Arms Up
    elbow_bent_max_deg: float = 120.0       # arm folded up near the head -> Wave

    # Bow: nose has dropped down close to the shoulder line as the upper
    # body pitches forward. Compared against shoulder width (not a fixed
    # pixel distance) so it stays roughly correct regardless of how far
    # you're standing from the camera.
    bow_head_gap_ratio: float = 0.55

    # How often (seconds) to re-sync the local mirror flag from the server's
    # /pose/status, and the minimum gap between HTTP posts for an unchanged pose.
    status_sync_interval: float = 1.0
    min_send_interval: float = 0.15


# -----------------------------
# Small HTTP helpers (stdlib only — no extra deps needed for this venv)
# -----------------------------

def post_json(base_url: str, path: str, payload: dict, timeout: float = 1.0) -> dict:
    url = base_url.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_json(base_url: str, path: str, timeout: float = 1.0) -> dict:
    url = base_url.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"available": False, "error": str(e)}


# -----------------------------
# Geometry / landmark helpers
# -----------------------------

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_angle_between_vectors(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a < 1e-6 or norm_b < 1e-6:
        return 0.0
    cos_value = float(np.dot(a, b) / (norm_a * norm_b))
    cos_value = clamp(cos_value, -1.0, 1.0)
    return math.degrees(math.acos(cos_value))


@dataclass
class Landmark2D:
    x: float
    y: float
    z: float
    visibility: float

    def as_np_xy(self) -> np.ndarray:
        return np.array([self.x, self.y], dtype=np.float32)


@dataclass
class UpperBodyLandmarks:
    left_shoulder: Landmark2D
    right_shoulder: Landmark2D
    left_elbow: Landmark2D
    right_elbow: Landmark2D
    left_wrist: Landmark2D
    right_wrist: Landmark2D
    nose: Landmark2D

    def min_visibility(self) -> float:
        # Deliberately arm-only (not nose) — a turned head shouldn't make
        # the whole frame register as NO_PERSON while arms are clearly visible.
        return min(
            self.left_shoulder.visibility, self.right_shoulder.visibility,
            self.left_elbow.visibility, self.right_elbow.visibility,
            self.left_wrist.visibility, self.right_wrist.visibility,
        )


@dataclass
class PoseMetrics:
    left_elbow_angle: float
    right_elbow_angle: float
    left_wrist_above_shoulder: bool
    right_wrist_above_shoulder: bool
    head_shoulder_gap: float   # shoulder midline y minus nose y; shrinks toward 0 when bowing
    shoulder_width: float
    nose_visible: bool


# -----------------------------
# MediaPipe sensing
# -----------------------------

class PoseSensingModule:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.mp_pose = mp.solutions.pose
        self.mp_draw = mp.solutions.drawing_utils
        self.pose = self.mp_pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            smooth_landmarks=True,
            enable_segmentation=False,
            min_detection_confidence=config.min_detection_confidence,
            min_tracking_confidence=config.min_tracking_confidence,
        )

    def process_frame(self, frame_bgr: np.ndarray) -> Tuple[np.ndarray, Optional[UpperBodyLandmarks], float]:
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        frame_rgb.flags.writeable = False
        results = self.pose.process(frame_rgb)
        frame_rgb.flags.writeable = True

        annotated = frame_bgr.copy()
        if not results.pose_landmarks:
            return annotated, None, 0.0

        self.mp_draw.draw_landmarks(annotated, results.pose_landmarks, self.mp_pose.POSE_CONNECTIONS)

        lm = results.pose_landmarks.landmark
        idx = self.mp_pose.PoseLandmark

        def get(point: Any) -> Landmark2D:
            p = lm[point.value]
            return Landmark2D(p.x, p.y, p.z, p.visibility)

        upper = UpperBodyLandmarks(
            left_shoulder=get(idx.LEFT_SHOULDER), right_shoulder=get(idx.RIGHT_SHOULDER),
            left_elbow=get(idx.LEFT_ELBOW), right_elbow=get(idx.RIGHT_ELBOW),
            left_wrist=get(idx.LEFT_WRIST), right_wrist=get(idx.RIGHT_WRIST),
            nose=get(idx.NOSE),
        )
        return annotated, upper, upper.min_visibility()

    def close(self):
        self.pose.close()


# -----------------------------
# Pose classification
# -----------------------------

class PoseClassifier:
    """
    Classifies landmarks into four categories that each map to a matching
    robot action (see pose_controller.py): Neutral, Arms Up, Wave, Bow.

    Wave and Bow are geometric, single-frame proxies for gestures that are
    naturally about motion — there's no frame-to-frame velocity tracking
    here, so we detect the STATIC SHAPE a person holds mid-gesture:
      - Wave: one hand raised near/above head height with the elbow folded
        (the shape a hand naturally holds while wobbling side to side).
      - Bow: the head has dropped down close to the shoulder line as the
        upper body pitches forward.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.previous_pose = UNKNOWN
        self.pose_stability_count: Dict[str, int] = {}
        self.required_stable_frames = 2

    def _metrics(self, body: UpperBodyLandmarks) -> PoseMetrics:
        ls, rs = body.left_shoulder.as_np_xy(), body.right_shoulder.as_np_xy()
        le, re = body.left_elbow.as_np_xy(), body.right_elbow.as_np_xy()
        lw, rw = body.left_wrist.as_np_xy(), body.right_wrist.as_np_xy()

        left_elbow_angle = safe_angle_between_vectors(ls - le, lw - le)
        right_elbow_angle = safe_angle_between_vectors(rs - re, rw - re)

        left_wrist_above_shoulder = lw[1] < (ls[1] - self.config.wrist_above_shoulder_margin)
        right_wrist_above_shoulder = rw[1] < (rs[1] - self.config.wrist_above_shoulder_margin)

        shoulder_mid_y = (ls[1] + rs[1]) / 2.0
        shoulder_width = float(abs(rs[0] - ls[0]))
        nose_visible = body.nose.visibility >= self.config.landmark_visibility_threshold
        head_shoulder_gap = float(shoulder_mid_y - body.nose.y)

        return PoseMetrics(
            left_elbow_angle=left_elbow_angle, right_elbow_angle=right_elbow_angle,
            left_wrist_above_shoulder=left_wrist_above_shoulder,
            right_wrist_above_shoulder=right_wrist_above_shoulder,
            head_shoulder_gap=head_shoulder_gap, shoulder_width=shoulder_width,
            nose_visible=nose_visible,
        )

    def classify_raw(self, body: Optional[UpperBodyLandmarks]) -> Tuple[str, Optional[PoseMetrics]]:
        if body is None:
            return NO_PERSON, None
        if body.min_visibility() < self.config.landmark_visibility_threshold:
            return NO_PERSON, None

        m = self._metrics(body)
        left_straight = m.left_elbow_angle >= self.config.elbow_straight_min_deg
        right_straight = m.right_elbow_angle >= self.config.elbow_straight_min_deg
        left_bent = m.left_elbow_angle < self.config.elbow_bent_max_deg
        right_bent = m.right_elbow_angle < self.config.elbow_bent_max_deg

        both_wrists_up = m.left_wrist_above_shoulder and m.right_wrist_above_shoulder
        only_left_up = m.left_wrist_above_shoulder and not m.right_wrist_above_shoulder
        only_right_up = m.right_wrist_above_shoulder and not m.left_wrist_above_shoulder

        # Arms Up: both hands raised, arms extended straight.
        if both_wrists_up and left_straight and right_straight:
            return ARMS_UP, m

        # Wave: exactly one hand raised, elbow folded (not extended straight
        # like a rigid one-arm salute) — the shape held while waving.
        if (only_left_up and left_bent) or (only_right_up and right_bent):
            return WAVE, m

        # Bow: head has dropped close to the shoulder line, relative to
        # shoulder width so it holds up regardless of distance from camera.
        if m.nose_visible and m.shoulder_width > 1e-3:
            if m.head_shoulder_gap < self.config.bow_head_gap_ratio * m.shoulder_width:
                return BOW, m

        ls_y, rs_y = body.left_shoulder.y, body.right_shoulder.y
        neutral_left = body.left_wrist.y > (ls_y - self.config.wrist_near_shoulder_margin)
        neutral_right = body.right_wrist.y > (rs_y - self.config.wrist_near_shoulder_margin)
        if neutral_left and neutral_right:
            return NEUTRAL, m

        return UNKNOWN, m

    def classify(self, body: Optional[UpperBodyLandmarks]) -> Tuple[str, Optional[PoseMetrics]]:
        raw_pose, metrics = self.classify_raw(body)

        self.pose_stability_count[raw_pose] = self.pose_stability_count.get(raw_pose, 0) + 1
        for pose in list(self.pose_stability_count.keys()):
            if pose != raw_pose:
                self.pose_stability_count[pose] = 0

        if self.pose_stability_count[raw_pose] >= self.required_stable_frames:
            self.previous_pose = raw_pose
            return raw_pose, metrics
        return self.previous_pose, metrics


# -----------------------------
# Client controller — webcam loop + HTTP relay to the Flask server
# -----------------------------

class PoseClient:
    def __init__(self, server_url: str, config: Config, demo: bool = False) -> None:
        self.server_url = server_url
        self.config = config
        self.demo = demo

        self.sensing = PoseSensingModule(config)
        self.classifier = PoseClassifier(config)

        self.enabled_local = False
        self.last_sent_pose: Optional[str] = None
        self.last_send_time = 0.0

    def sync_enabled(self) -> None:
        if self.demo:
            return
        status = get_json(self.server_url, "/pose/status")
        if status.get("available"):
            self.enabled_local = bool(status.get("enabled"))

    def toggle(self) -> None:
        if self.demo:
            self.enabled_local = not self.enabled_local
            print("  [demo] mimic toggled ->", "ON" if self.enabled_local else "OFF")
            return
        path = "/pose/stop" if self.enabled_local else "/pose/start"
        r = post_json(self.server_url, path, {})
        if r.get("ok"):
            self.enabled_local = not self.enabled_local
        else:
            print("  [pose_client] toggle failed:", r.get("error"))

    def send_neutral(self) -> None:
        if self.demo:
            print("  [demo] neutral")
            return
        post_json(self.server_url, "/pose/relay", {"event": "neutral"})

    def report_pose(self, pose_label: str) -> None:
        now = time.time()
        if pose_label == self.last_sent_pose and (now - self.last_send_time) < self.config.min_send_interval:
            return
        self.last_sent_pose = pose_label
        self.last_send_time = now
        if self.demo:
            print("  [demo] pose ->", pose_label)
            return
        post_json(self.server_url, "/pose/relay", {"event": "pose", "pose": pose_label})

    def _overlay(self, frame, pose_label, fps, min_visibility) -> None:
        lines = [
            "Mimic: {}".format("ON" if self.enabled_local else "OFF"),
            "Pose: {}".format(pose_label),
            "FPS: {:.1f}".format(fps),
            "Visibility: {:.2f}".format(min_visibility),
            "Server: {}".format("DEMO (no server)" if self.demo else self.server_url),
            "Keys: s=start/stop  n=neutral  q=quit",
        ]
        y = 30
        for line in lines:
            cv2.putText(frame, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            y += 26

    def run(self) -> None:
        cap = cv2.VideoCapture(self.config.camera_index)
        if not cap.isOpened():
            raise RuntimeError("Could not open webcam index {}".format(self.config.camera_index))

        if not self.demo:
            self.sync_enabled()

        last_sync = time.time()
        prev_time = time.time()
        fps = 0.0

        print("Pose client running — keys: s=start/stop mirror, n=neutral, q=quit")
        print("Mode:", "DEMO (no HTTP calls)" if self.demo else "LIVE -> " + self.server_url)

        try:
            while True:
                now = time.time()
                if not self.demo and (now - last_sync) > self.config.status_sync_interval:
                    self.sync_enabled()
                    last_sync = now

                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue

                frame = cv2.flip(frame, 1)
                annotated, body, min_visibility = self.sensing.process_frame(frame)
                pose_label, metrics = self.classifier.classify(body)

                if self.enabled_local:
                    self.report_pose(pose_label)

                dt = now - prev_time
                prev_time = now
                if dt > 0:
                    fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps else (1.0 / dt)

                self._overlay(annotated, pose_label, fps, min_visibility)
                cv2.imshow(WINDOW_NAME, annotated)

                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    print("  'q' pressed — closing pose window")
                    break
                elif key == ord("s"):
                    self.toggle()
                elif key == ord("n"):
                    self.send_neutral()

        finally:
            cap.release()
            cv2.destroyAllWindows()
            self.sensing.close()
            print("  Camera released.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AlphaMini MimicMini pose-detection client")
    parser.add_argument("--server", type=str, default="http://127.0.0.1:5050",
                         help="Base URL of the Flask server started by main.py")
    parser.add_argument("--camera", type=int, default=0, help="Webcam index")
    parser.add_argument("--demo", action="store_true",
                         help="Run detection only — don't call the server at all")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = Config(camera_index=args.camera)
    client = PoseClient(server_url=args.server, config=config, demo=args.demo)
    client.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except Exception as exc:
        print("pose_client crashed:", exc)
        raise
