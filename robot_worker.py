"""
robot_worker.py
────────────────
One subprocess per Alpha Mini.

FIX: Now properly calls quit_program() + release() on shutdown so the
robot lets go of the session and can reconnect on next launch.
"""

import asyncio
import logging
import signal
import threading
import time

import mini.mini_sdk as MiniSdk
from mini.apis.api_action import PlayAction, MoveRobot, MoveRobotDirection, StopAllAction
from mini.apis.api_expression import PlayExpression, SetMouthLamp, MouthLampColor, MouthLampMode
from mini.apis.api_observe import ObserveFaceDetect
from mini.apis.api_sound import PlayAudio

import mini.apis.api_sound as api_sound
TTS_CLASS = None
for _name in dir(api_sound):
    _n = _name.lower()
    if "tts" in _n and "request" not in _n and "response" not in _n:
        TTS_CLASS = getattr(api_sound, _name)
        break


async def _say(text):
    if not text or TTS_CLASS is None:
        return None
    try:
        obj = TTS_CLASS(text=text)
    except TypeError:
        try:
            obj = TTS_CLASS(tts_text=text)
        except Exception:
            return None
    return asyncio.create_task(obj.execute())


async def _execute_command(serial, cmd):
    execute_at = cmd.get("execute_at")
    if execute_at:
        delay = execute_at - time.time()
        if delay > 0:
            await asyncio.sleep(delay)

    t = cmd.get("type")
    print("  [{}] executing: type={} name={}".format(serial, t, cmd.get("name", "")))

    if t == "action":
        await PlayAction(is_serial=cmd.get("wait", False), action_name=cmd["name"]).execute()

    elif t == "action_with_sound":
        tts_task = await _say(cmd.get("tts", ""))
        action_task = asyncio.create_task(
            PlayAction(is_serial=cmd.get("wait", False), action_name=cmd["name"]).execute()
        )
        await asyncio.gather(
            action_task,
            *([] if tts_task is None else [tts_task]),
            return_exceptions=True,
        )

    elif t == "move":
        direction = MoveRobotDirection[cmd["direction"]]
        await MoveRobot(direction=direction, step=cmd.get("step", 1)).execute()

    elif t == "expression":
        await PlayExpression(express_name=cmd["name"]).execute()

    elif t == "lamp":
        color = MouthLampColor[cmd["color"]]
        mode  = MouthLampMode[cmd["mode"]]
        await SetMouthLamp(
            color=color, mode=mode,
            duration=cmd.get("duration", 2000),
            breath_duration=cmd.get("breath_duration", 800),
        ).execute()

    elif t == "tts":
        tts_task = await _say(cmd["text"])
        if tts_task:
            try:
                await tts_task
            except Exception:
                pass

    elif t == "audio":
        await PlayAudio(
            audio_name=cmd.get("audio_name", ""),
            audio_url=cmd.get("url", ""),
        ).execute()

    elif t == "stop_all":
        await StopAllAction().execute()

    elif t == "sleep":
        await asyncio.sleep(cmd["seconds"])


class FaceDetectTask:
    def __init__(self, status_q, serial, cooldown=5.0):
        self.status_q  = status_q
        self.serial    = serial
        self.cooldown  = cooldown
        self.observer  = None
        self.last_fire = 0.0

    def start(self):
        self.observer = ObserveFaceDetect()
        def on_face(response):
            count = getattr(response, "count", 0)
            now   = time.time()
            if count > 0 and (now - self.last_fire) > self.cooldown:
                self.last_fire = now
                try:
                    self.status_q.put({"event": "face_seen", "serial": self.serial, "count": count})
                except Exception:
                    pass
        if hasattr(self.observer, "set_handler"):
            try:
                self.observer.set_handler(on_face)
            except Exception:
                pass
        asyncio.create_task(self.observer.execute())

    def stop(self):
        if self.observer:
            try:
                self.observer.stop()
            except Exception:
                pass
            self.observer = None


async def _clean_disconnect(serial):
    """Properly release the robot so it accepts new connections next time."""
    print("[{}] Releasing robot session...".format(serial))
    # 1. Stop any ongoing action
    try:
        await asyncio.wait_for(StopAllAction().execute(), timeout=2.0)
    except Exception:
        pass
    # 2. Exit program mode (very important — this is what frees the robot!)
    try:
        if hasattr(MiniSdk, "quit_program"):
            await asyncio.wait_for(MiniSdk.quit_program(), timeout=3.0)
    except Exception as e:
        print("[{}] quit_program warning: {}".format(serial, e))
    # 3. Release the connection
    try:
        await asyncio.wait_for(MiniSdk.release(), timeout=3.0)
    except Exception as e:
        print("[{}] release warning: {}".format(serial, e))
    print("[{}] ✓ Cleanly disconnected".format(serial))


async def _worker_async(serial, cmd_q, status_q, robot_type_name="EDU"):
    MiniSdk.set_log_level(logging.ERROR)
    MiniSdk.set_robot_type(getattr(MiniSdk.RobotType, robot_type_name))

    print("[{}] Searching...".format(serial))
    device = await MiniSdk.get_device_by_name(serial, 15)
    if not device:
        status_q.put({"event": "connect_failed", "serial": serial, "reason": "not_found"})
        return

    print("[{}] Connecting...".format(serial))
    try:
        ok = await MiniSdk.connect(device)
    except Exception as e:
        status_q.put({"event": "connect_failed", "serial": serial, "reason": str(e)})
        return

    if not ok:
        status_q.put({"event": "connect_failed", "serial": serial, "reason": "refused"})
        return

    await asyncio.sleep(2)
    try:
        await MiniSdk.enter_program()
    except Exception:
        pass

    print("[{}] READY".format(serial))
    status_q.put({"event": "ready", "serial": serial})

    loop    = asyncio.get_running_loop()
    async_q = asyncio.Queue()

    def reader():
        while True:
            try:
                item = cmd_q.get()
                if item is None:
                    break
                asyncio.run_coroutine_threadsafe(async_q.put(item), loop)
                if item.get("type") == "quit":
                    break
            except Exception:
                break

    threading.Thread(target=reader, daemon=True).start()
    face_task = None

    try:
        while True:
            cmd   = await async_q.get()
            ctype = cmd.get("type")

            if ctype == "quit":
                break
            if ctype == "start_face_detect":
                if not face_task:
                    face_task = FaceDetectTask(status_q, serial, cooldown=cmd.get("cooldown", 5.0))
                    face_task.start()
                continue
            if ctype == "stop_face_detect":
                if face_task:
                    face_task.stop()
                    face_task = None
                continue

            try:
                await _execute_command(serial, cmd)
            except Exception as e:
                print("  [{}] ERROR executing {}: {}".format(serial, ctype, e))
                status_q.put({"event": "error", "serial": serial, "cmd": ctype, "error": str(e)})
    finally:
        if face_task:
            face_task.stop()
        await _clean_disconnect(serial)


def worker_main(serial, cmd_q, status_q, robot_type_name="EDU"):
    # Ignore SIGINT in worker — orchestrator sends {"type":"quit"} for clean shutdown
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        asyncio.run(_worker_async(serial, cmd_q, status_q, robot_type_name))
    except KeyboardInterrupt:
        pass
    except Exception as e:
        try:
            status_q.put({"event": "crash", "serial": serial, "error": str(e)})
        except Exception:
            pass
