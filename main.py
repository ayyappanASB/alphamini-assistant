"""
main.py
────────
Entry point — starts 1 Alpha Mini worker and the Flask web server, prints
the QR code, and hosts the SERVER-SIDE half of MimicMini pose mirroring
(rate limiting, action-name mapping, Web-UI-priority gating).

This process deliberately does NOT import cv2 / mediapipe. The webcam +
MediaPipe pose window lives in the separate script `pose_client.py`, run
in its OWN Python environment, because mediapipe needs protobuf>=4.25,<5
while the AlphaMini SDK needs protobuf==3.20.3 — the two cannot coexist in
one interpreter (see README for the full explanation). `pose_client.py`
talks to this Flask server over local HTTP (POST /pose/relay); it never
touches the robot directly, so there is still only ONE Mini SDK connection
in the whole system, owned entirely by this process's Orchestrator.

Threading model:
    - Main thread     : Flask (app.run) — owns the process's signal
                         handling / stdin loop.
    - Orchestrator     : 1 multiprocessing.Process per robot (unchanged).
    - Pose watchdog     : 1 daemon thread inside PoseMimicController that
                         sends a fail-safe neutral if pose_client.py stops
                         reporting (tracking lost, or client closed/crashed).

FIX: Ctrl+C now waits for the worker to properly release the robot before
exiting, so the robot can reconnect next launch (no more "Searching..." lock).

Run:
    python main.py --no-tunnel

Then, in a SECOND terminal with the SECOND venv (requirements-vision.txt):
    python pose_client.py --server http://127.0.0.1:5050

Useful flags:
    --no-pose        Disable the pose-mimic gating routes entirely
                      (/pose/* return 503) — use if you don't want pose
                      mirroring available on this server at all.
    --pose-demo      Enable pose gating but only PRINT the action that
                      would be sent instead of sending it to the robot —
                      use this to sanity-check the flow before your action
                      names are confirmed on the real robot.
"""

import argparse
import multiprocessing as mp
import os
import signal
import socket
import subprocess
import sys
import threading
import time

from orchestrator import Orchestrator
from webserver import create_app
import pose_controller as pose_controller_module

# ─── EDIT THIS SERIAL TO MATCH YOUR ROBOT ────────────────────────────────────
ROBOT_SERIALS = ["YOUR_ROBOT_SERIAL"]   # Replace with your Alpha Mini serial/name
# ─────────────────────────────────────────────────────────────────────────────

ROBOT_TYPE = "EDU"
FLASK_PORT = 5050

_orch = None        # global handle for signal handler
_pose_ctrl = None    # global handle for signal handler
_shutdown_event = threading.Event()


def banner(text):
    print("\n" + "═" * 60)
    print("  " + text)
    print("═" * 60 + "\n")


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def print_qr(url):
    print("\n  SCAN WITH YOUR PHONE:\n")
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        print("  (pip install qrcode[pil] to see QR ASCII art)")
    print("\n  URL: {}\n".format(url))


def start_cloudflare_tunnel():
    banner("STARTING CLOUDFLARE TUNNEL")
    cf_path = os.path.join(os.getcwd(), "cloudflared.exe")
    if not os.path.exists(cf_path):
        print("❌ cloudflared.exe not found in this directory!")
        _fallback_local()
        return
    cmd = [cf_path, "tunnel", "--url", "http://localhost:{}".format(FLASK_PORT)]
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        print("  ✅ Cloudflare process initialized. Generating public URL...")
        for line in iter(process.stdout.readline, ""):
            if "trycloudflare.com" in line:
                for part in line.split():
                    if "trycloudflare.com" in part:
                        url = part.strip().replace("url=", "")
                        if not url.startswith("http"):
                            url = "https://" + url
                        print("  🚀 CLOUDFLARE TUNNEL ONLINE")
                        print_qr(url)
                        break
    except Exception as e:
        print("❌ Failed running Cloudflare: {}".format(e))
        _fallback_local()


def _fallback_local():
    url = "http://{}:{}".format(get_local_ip(), FLASK_PORT)
    banner("⚠  LOCAL IP FALLBACK (LAN ONLY)")
    print("  URL: {}".format(url))
    print_qr(url)


def clean_shutdown():
    """Stop the pose watchdog thread, then give the worker time to release the robot."""
    global _orch, _pose_ctrl
    banner("SHUTTING DOWN — releasing robot session...")

    _shutdown_event.set()
    if _pose_ctrl is not None:
        try:
            _pose_ctrl.stop()
        except Exception as e:
            print("  Pose shutdown warning: {}".format(e))

    if _orch is not None:
        try:
            _orch.shutdown(timeout=8)   # ≥ quit_program + release timeouts in worker
        except Exception as e:
            print("  Shutdown error: {}".format(e))
    print("  ✓ Bye!")
    os._exit(0)


def signal_handler(signum, frame):
    clean_shutdown()


def terminal_admin_loop():
    while True:
        try:
            raw = input().strip()
            if raw.lower() in ("q", "quit", "exit"):
                clean_shutdown()
        except (EOFError, KeyboardInterrupt):
            break


def main():
    global _orch, _pose_ctrl

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-tunnel",       action="store_true")
    parser.add_argument("--connect-timeout", type=int, default=30)
    parser.add_argument("--no-pose",         action="store_true",
                         help="Disable pose-mimic gating routes entirely (/pose/* return 503)")
    parser.add_argument("--pose-demo",       action="store_true",
                         help="Pose gating active, but only print robot commands instead of sending them")
    args = parser.parse_args()

    banner("ALPHA MINI 1-ROBOT SHOW SERVER + MIMICMINI POSE MIRROR")
    print("  Robot to connect: {}".format(ROBOT_SERIALS))

    # Install signal handlers for clean shutdown
    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    _orch = Orchestrator(ROBOT_SERIALS, robot_type_name=ROBOT_TYPE)
    _orch.start()
    _orch.wait_for_ready(timeout=args.connect_timeout)
    _orch.print_summary()

    if not args.no_pose:
        _pose_ctrl = pose_controller_module.PoseMimicController(
            orchestrator=_orch,
            shutdown_event=_shutdown_event,
            get_targets=lambda: [h for h in _orch.handles if h.ready],
            config=pose_controller_module.Config(),
            demo_mode=args.pose_demo,
        )
    else:
        _pose_ctrl = None

    app = create_app(_orch, _pose_ctrl)

    if args.no_tunnel:
        _fallback_local()
    else:
        threading.Thread(target=start_cloudflare_tunnel, daemon=True).start()

    threading.Thread(target=terminal_admin_loop, daemon=True).start()

    if _pose_ctrl is not None:
        banner("POSE MIMIC READY — start the camera on this laptop with:\n"
               "    python pose_client.py --server http://127.0.0.1:{}".format(FLASK_PORT))
        if args.pose_demo:
            print("  [pose] DEMO MODE — actions will be printed, not sent to the robot")
    else:
        print("  Pose mimic disabled (--no-pose)")

    banner("SERVER READY — Press 'q'+Enter (or Ctrl+C) to quit cleanly")
    try:
        app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, use_reloader=False)
    finally:
        clean_shutdown()


if __name__ == "__main__":
    mp.freeze_support()
    main()
