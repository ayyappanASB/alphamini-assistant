"""
orchestrator.py
────────────────
Manages one worker subprocess per robot, plus a phase runner thread.

FIX: shutdown() now gives workers extra time to properly release
the robot session (quit_program + release) before terminating.

ADDED: shared "busy" bookkeeping (robot_busy_until / is_busy / mark_busy /
try_reserve / try_send) so the Flask web UI and the pose-mimic thread can
safely coordinate access to the same robot without both sending commands
at once. Web UI manual controls always take priority — pose mimic only
uses the non-blocking try_send() path and simply skips a frame if the
robot is reserved or a phase is running.
"""

import multiprocessing as mp
import queue as queue_module
import threading
import time

from robot_worker import worker_main


class RobotHandle:
    def __init__(self, index, serial):
        self.index       = index
        self.serial      = serial
        self.cmd_queue   = mp.Queue()
        self.process     = None
        self.ready       = False
        self.failed      = False
        self.fail_reason = None

    @property
    def name(self):
        return "Robot-{}({})".format(self.index + 1, self.serial)


class PhaseInterrupted(Exception):
    pass


class Orchestrator:
    def __init__(self, serials, robot_type_name="EDU"):
        self.serials         = list(serials)
        self.robot_type_name = robot_type_name
        self.handles         = [RobotHandle(i, s) for i, s in enumerate(self.serials)]
        self.status_queue    = mp.Queue()
        self._listening      = False
        self._next_cmd_id    = 0
        self._cmd_id_lock    = threading.Lock()

        self.stop_event    = threading.Event()
        self.phase_thread  = None
        self.current_phase = "idle"
        self.phase_lock    = threading.Lock()

        # ─── shared busy/coordination state (Web UI <-> pose mimic) ──────────
        self.coord_lock       = threading.Lock()
        self.robot_busy_until = {h.index: 0.0 for h in self.handles}

    def start(self):
        for h in self.handles:
            h.process = mp.Process(
                target=worker_main,
                args=(h.serial, h.cmd_queue, self.status_queue, self.robot_type_name),
                daemon=False,   # NOT daemon — so we can join and let it release cleanly
            )
            h.process.start()

        self._listening = True
        threading.Thread(target=self._status_listener, daemon=True).start()

    def wait_for_ready(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            done = sum(1 for h in self.handles if h.ready or h.failed)
            if done == len(self.handles):
                break
            time.sleep(0.2)
        return [h for h in self.handles if h.ready]

    def print_summary(self):
        print("\n  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  ROBOT CONNECTION SUMMARY")
        print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        for h in self.handles:
            if h.ready:
                print("  ✓ {} -- READY".format(h.name))
            elif h.failed:
                print("  ✗ {} -- FAILED ({})".format(h.name, h.fail_reason))
            else:
                print("  ? {} -- still connecting".format(h.name))
        print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    def shutdown(self, timeout=8):
        """Ask workers to quit, wait for them to release the robot."""
        self._listening = False
        self.stop_event.set()

        # Ask nicely first
        for h in self.handles:
            try:
                h.cmd_queue.put({"type": "quit"})
            except Exception:
                pass

        # Wait for clean shutdown (worker calls quit_program + release inside)
        deadline = time.time() + timeout
        for h in self.handles:
            if h.process:
                remaining = max(0.5, deadline - time.time())
                h.process.join(timeout=remaining)

        # If any still alive after timeout, terminate as last resort
        for h in self.handles:
            if h.process and h.process.is_alive():
                print("  [!] {} did not release in time — terminating".format(h.name))
                h.process.terminate()
                h.process.join(timeout=2)

    def _status_listener(self):
        while self._listening:
            try:
                msg = self.status_queue.get(timeout=0.5)
            except queue_module.Empty:
                continue
            except Exception:
                break

            ev     = msg.get("event")
            serial = msg.get("serial")
            handle = next((h for h in self.handles if h.serial == serial), None)

            if ev == "ready" and handle:
                handle.ready = True
            elif ev == "connect_failed" and handle:
                handle.failed      = True
                handle.fail_reason = msg.get("reason", "unknown")
            elif ev == "crash" and handle:
                handle.failed      = True
                handle.fail_reason = "crash: " + msg.get("error", "?")
            elif ev == "error":
                print("  [!] {} cmd={} err: {}".format(serial, msg.get("cmd"), msg.get("error")))

    def connected_robots(self):
        return [h for h in self.handles if h.ready]

    def robot(self, index):
        for h in self.handles:
            if h.index == index:
                return h
        return None

    def _next_id(self):
        with self._cmd_id_lock:
            self._next_cmd_id += 1
            return self._next_cmd_id

    def send(self, handle, cmd):
        if handle is None or not handle.ready:
            print("  [SKIP] send() called on unready robot")
            return
        c = dict(cmd)
        c["id"] = self._next_id()
        print("  [→ {}] {}".format(handle.name, c))
        handle.cmd_queue.put(c)

    def broadcast(self, cmd, robots=None):
        targets = robots if robots is not None else self.connected_robots()
        for h in targets:
            self.send(h, cmd)

    def broadcast_sync(self, cmd, delay=1.5, robots=None):
        targets = robots if robots is not None else self.connected_robots()
        execute_time = time.time() + delay
        for h in targets:
            c = dict(cmd)
            c["execute_at"] = execute_time
            self.send(h, c)

    def all_stop(self):
        self.broadcast({"type": "stop_all"})

    def is_phase_running(self):
        return self.phase_thread is not None and self.phase_thread.is_alive()

    def start_phase(self, name, runner_fn, **kwargs):
        with self.phase_lock:
            if self.is_phase_running():
                return False, "Another phase is running: " + self.current_phase
            self.stop_event.clear()
            self.current_phase = name

            def wrapper():
                try:
                    runner_fn(self, **kwargs)
                except PhaseInterrupted:
                    print("  Phase '{}' interrupted".format(name))
                except Exception as e:
                    print("  Phase '{}' error: {}".format(name, e))
                finally:
                    self.current_phase = "idle"

            self.phase_thread = threading.Thread(target=wrapper, daemon=True)
            self.phase_thread.start()
            return True, "Started: " + name

    def stop_phase(self):
        self.stop_event.set()
        self.all_stop()
        return True, "Stop signaled"

    def hold(self, seconds):
        if self.stop_event.wait(seconds):
            raise PhaseInterrupted()

    # ─── shared busy/coordination helpers ────────────────────────────────────
    # Both webserver.py (Web UI) and pose_controller.py (pose mimic) read/write
    # this SAME state through the orchestrator, so they never fight over the
    # robot. Web UI keeps using explicit is_busy()/try_reserve() calls so it
    # can return a precise "busy, N seconds left" error to the phone. Pose
    # mimic uses the non-blocking try_send() wrapper and just skips a frame
    # if it can't get a reservation.

    def is_busy(self, handle):
        return time.time() < self.robot_busy_until.get(handle.index, 0.0)

    def busy_remaining(self, handle):
        return max(0.0, self.robot_busy_until.get(handle.index, 0.0) - time.time())

    def mark_busy(self, handle, seconds):
        with self.coord_lock:
            self.robot_busy_until[handle.index] = time.time() + seconds

    def clear_busy(self, handle):
        with self.coord_lock:
            self.robot_busy_until[handle.index] = 0.0

    def try_reserve(self, handle, seconds):
        """Atomically check-and-mark busy. Returns True if the reservation succeeded."""
        with self.coord_lock:
            if time.time() < self.robot_busy_until.get(handle.index, 0.0):
                return False
            self.robot_busy_until[handle.index] = time.time() + seconds
            return True

    def try_send(self, handle, cmd, busy_seconds=0.0):
        """
        Non-blocking, priority-respecting send used by pose mimic:
        skips (returns False) if a phase is running or the robot is
        currently reserved by the Web UI (or a previous pose command).
        """
        if self.is_phase_running():
            return False
        if busy_seconds > 0:
            if not self.try_reserve(handle, busy_seconds):
                return False
        elif self.is_busy(handle):
            return False
        self.send(handle, cmd)
        return True
