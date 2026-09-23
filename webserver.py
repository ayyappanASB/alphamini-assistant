"""
webserver.py
─────────────
Flask web server for Alpha Mini control (1 robot, simplified).
No claim/control system — any device can hit any button immediately.
Self-contained: phase runners (wave / march / dance) are inlined below.

ADDED (MimicMini integration):
    - GET  /pose/status  — live pose-mimic status for the status panel
    - POST /pose/start   — enable pose mirroring (same as pressing 's')
    - POST /pose/stop    — disable pose mirroring (same as pressing 's')
    - Emergency /stop now also disables pose mirroring.
    - "busy" cool-downs now live on the Orchestrator (shared with pose
      mimic) instead of a local dict, so the two can't fight over the robot.
"""

import time
import os
import secrets

from flask import Flask, render_template_string, jsonify, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from actions import ACTIONS, build_action_command, get_grouped_actions
from content_filter import find_violation
from orchestrator import PhaseInterrupted


ACTION_LOCK_TIME = 3.5      # cool-down after a normal action
DANCE_LOCK_TIME  = 25.0     # dances are long — give them time
MOVE_LOCK_TIME   = 1.5      # cool-down after a move step


# ═════════════════════════════════════════════════════════════════════════════
# INLINE PHASE RUNNERS
# ═════════════════════════════════════════════════════════════════════════════

def run_wave_phase(orch, targets, duration=120.0):
    end = time.time() + duration
    while time.time() < end:
        if orch.stop_event.is_set():
            raise PhaseInterrupted()
        orch.broadcast_sync(
            {"type": "action_with_sound", "name": "Surveillance_003",
             "tts": "Hello everyone!", "wait": False},
            delay=1.0, robots=targets,
        )
        orch.hold(6.0)


def run_march_phase(orch, targets, duration=60.0):
    end = time.time() + duration
    while time.time() < end:
        if orch.stop_event.is_set():
            raise PhaseInterrupted()
        orch.broadcast_sync(
            {"type": "move", "direction": "FORWARD", "step": 3},
            delay=1.0, robots=targets,
        )
        orch.hold(4.0)
        orch.broadcast_sync(
            {"type": "action_with_sound", "name": "012",
             "tts": "One, two, three, four!", "wait": False},
            delay=1.0, robots=targets,
        )
        orch.hold(6.0)


def run_dance_phase(orch, targets):
    routine = [
        ("dance_0001en", 25.0),
        ("dance_0004en", 25.0),
        ("dance_0008en", 20.0),
    ]
    for name, hold_time in routine:
        if orch.stop_event.is_set():
            raise PhaseInterrupted()
        orch.broadcast_sync(
            {"type": "action", "name": name, "wait": False},
            delay=1.0, robots=targets,
        )
        orch.hold(hold_time)


# ═════════════════════════════════════════════════════════════════════════════
# FLASK APP FACTORY
# ═════════════════════════════════════════════════════════════════════════════

def create_app(orchestrator, pose_controller=None):
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
    CORS(app, supports_credentials=True)

    n_robots = len(orchestrator.handles)

    def all_ready_robots():
        """Return every ready robot — no ownership check."""
        return [h for h in orchestrator.handles if h.ready]

    # Stubs so main.py stays compatible
    app.kick_by_index = lambda idx: "N/A"
    app.print_devices = lambda: None

    # ─── routes ──────────────────────────────────────────────────────────────

    @app.route("/")
    def index():
        return render_template_string(
            HTML_TEMPLATE,
            grouped=get_grouped_actions(kids_only=True),
            n_robots=n_robots,
            pose_available=pose_controller is not None,
        )

    @app.route("/status")
    def status():
        return jsonify({
            "n_ready":       len(all_ready_robots()),
            "n_total":       n_robots,
            "current_phase": orchestrator.current_phase,
            "phase_running": orchestrator.is_phase_running(),
        })

    @app.route("/phase/<name>", methods=["POST"])
    def phase(name):
        targets = all_ready_robots()
        if not targets:
            return jsonify({"ok": False, "error": "No robot online."}), 503

        if name == "stop":
            orchestrator.stop_phase()
            for h in targets:
                orchestrator.clear_busy(h)
            return jsonify({"ok": True, "message": "Phase stopped"})

        runner_map = {"wave": run_wave_phase, "march": run_march_phase, "dance": run_dance_phase}
        runner = runner_map.get(name)
        if not runner:
            return jsonify({"ok": False, "error": "Unknown phase"}), 400

        kwargs = {"targets": targets}
        if name == "wave":
            kwargs.update({"duration": 120.0})

        ok_, msg = orchestrator.start_phase(name, runner, **kwargs)
        return jsonify({"ok": ok_, "error": msg} if not ok_ else {"ok": True, "message": msg})

    @app.route("/action", methods=["POST"])
    def action():
        if orchestrator.is_phase_running():
            return jsonify({"ok": False, "error": "A phase is currently running!"}), 429

        data = request.get_json() or {}
        action_key = data.get("action")
        targets = all_ready_robots()
        if not targets:
            return jsonify({"ok": False, "error": "No robot online"}), 503

        spec = ACTIONS.get(action_key, {})
        is_dance = spec.get("kind") == "dance"
        lock_time = DANCE_LOCK_TIME if is_dance else ACTION_LOCK_TIME

        for h in targets:
            if orchestrator.is_busy(h):
                remaining = int(orchestrator.busy_remaining(h))
                return jsonify({"ok": False,
                                "error": "Robot is busy ({}s left)".format(remaining)}), 429

        cmds = build_action_command(action_key, with_sound=True)
        for h in targets:
            orchestrator.mark_busy(h, lock_time)
            for c in cmds:
                orchestrator.send(h, c)

        return jsonify({"ok": True, "message": "Action sent"})

    @app.route("/tts", methods=["POST"])
    def tts():
        if orchestrator.is_phase_running():
            return jsonify({"ok": False, "error": "A phase is currently running!"}), 429

        data = request.get_json() or {}
        text = (data.get("text") or "").strip()
        targets = all_ready_robots()
        if not targets:
            return jsonify({"ok": False, "error": "No robot online"}), 503

        for h in targets:
            if orchestrator.is_busy(h):
                return jsonify({"ok": False, "error": "Robot is busy!"}), 429

        if find_violation(text):
            return jsonify({"ok": False, "error": "Filtered", "blocked": True}), 400

        for h in targets:
            orchestrator.mark_busy(h, ACTION_LOCK_TIME)
            orchestrator.send(h, {"type": "tts", "text": text})

        return jsonify({"ok": True, "message": "Speaking"})

    @app.route("/move", methods=["POST"])
    def move():
        if orchestrator.is_phase_running():
            return jsonify({"ok": False, "error": "A phase is currently running!"}), 429

        data = request.get_json() or {}
        direction = data.get("direction", "")
        targets = all_ready_robots()
        if not targets:
            return jsonify({"ok": False, "error": "No robot online"}), 503

        dir_map = {"forward": "FORWARD", "backward": "BACKWARD",
                   "left": "LEFTWARD", "right": "RIGHTWARD"}

        for h in targets:
            if direction == "stop":
                orchestrator.send(h, {"type": "stop_all"})
                orchestrator.clear_busy(h)
            else:
                if direction not in dir_map:
                    return jsonify({"ok": False, "error": "Bad direction"}), 400
                if orchestrator.is_busy(h):
                    return jsonify({"ok": False, "error": "Robot is busy!"}), 429
                orchestrator.mark_busy(h, MOVE_LOCK_TIME)
                orchestrator.send(h, {"type": "move",
                                       "direction": dir_map[direction], "step": 3})

        return jsonify({"ok": True, "message": "Moving"})

    @app.route("/stop", methods=["POST"])
    def emergency_stop():
        targets = all_ready_robots()
        orchestrator.stop_phase()

        # Emergency stop takes priority over everything, including pose mimic.
        if pose_controller is not None:
            pose_controller.disable(send_neutral=False)  # neutral sent below instead

        execute_time = time.time() + 0.5
        for h in targets:
            orchestrator.clear_busy(h)
            orchestrator.send(h, {"type": "stop_all"})
            orchestrator.send(h, {"type": "action", "name": "009",
                                   "wait": False, "execute_at": execute_time})

        return jsonify({"ok": True, "message": "STOPPED & RESETTING TO IDLE"})

    # ─── pose-mimic routes ──────────────────────────────────────────────────

    @app.route("/pose/status")
    def pose_status():
        if pose_controller is None:
            return jsonify({"available": False})
        return jsonify(pose_controller.get_status())

    @app.route("/pose/start", methods=["POST"])
    def pose_start():
        if pose_controller is None:
            return jsonify({"ok": False, "error": "Pose mimic not available on this server"}), 503
        pose_controller.enable()
        return jsonify({"ok": True, "message": "Pose mimic ON"})

    @app.route("/pose/stop", methods=["POST"])
    def pose_stop():
        if pose_controller is None:
            return jsonify({"ok": False, "error": "Pose mimic not available on this server"}), 503
        pose_controller.disable()
        return jsonify({"ok": True, "message": "Pose mimic OFF"})

    @app.route("/pose/relay", methods=["POST"])
    def pose_relay():
        """
        Called by the separate pose_client.py process (its own venv — see
        README) instead of importing the orchestrator directly. Keeps the
        conflicting mediapipe/protobuf dependency out of this process while
        still funneling every command through the same Orchestrator.
        """
        if pose_controller is None:
            return jsonify({"ok": False, "error": "Pose mimic not available on this server"}), 503

        data = request.get_json() or {}
        event = data.get("event")

        if event == "pose":
            result = pose_controller.report_pose(data.get("pose", "UNKNOWN"))
            return jsonify(result)
        elif event == "neutral":
            result = pose_controller.force_neutral()
            return jsonify(result)

        return jsonify({"ok": False, "error": "Unknown event"}), 400

    return app


# ═════════════════════════════════════════════════════════════════════════════
# HTML / JS TEMPLATE
# ═════════════════════════════════════════════════════════════════════════════
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>{{ n_robots }}-Robot Control</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Share+Tech+Mono&display=swap');
  :root{ --bg:#050810; --surface:#0c1120; --surface2:#101730; --border:#1a2a4a; --accent:#00d4ff; --danger:#ff3366; --ok:#00ff88; --text:#e0f0ff; --dim:#4a6080; }
  *{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  body{background:var(--bg);color:var(--text);font-family:'Share Tech Mono',monospace;min-height:100dvh;display:flex;flex-direction:column;padding:10px;gap:8px;padding-bottom:80px;}
  header{display:flex;align-items:center;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--border)}
  .logo{font-family:'Orbitron',sans-serif;font-weight:900;font-size:.95rem;color:var(--accent);}
  .status-pill{display:flex;align-items:center;gap:6px;font-size:.6rem;padding:3px 8px;border-radius:20px;border:1px solid var(--border);background:var(--surface)}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--dim)} .dot.on{background:var(--ok);} .dot.err{background:var(--danger);}

  .fb{background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:.65rem;color:var(--accent);text-align:center}
  .fb.err{color:var(--danger);border-color:var(--danger)} .fb.ok{color:var(--ok);border-color:var(--ok)} .fb.warn{color:#ffcc00;border-color:#ffcc00}

  .statusbar{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:8px 10px;font-size:.62rem}
  .statusbar div span{color:var(--accent)}

  .tabs{display:flex;gap:4px;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:4px}
  .tab{flex:1;text-align:center;padding:8px 4px;font-family:'Orbitron',sans-serif;font-size:.6rem;cursor:pointer;border-radius:4px;color:var(--dim);background:transparent;border:none}
  .tab.active{background:rgba(0,212,255,.1);color:var(--accent);}

  .panel{display:none;flex-direction:column;gap:8px;transition:opacity .2s}
  .panel.active{display:flex}
  .panel.locked{opacity:.4;pointer-events:none;}

  .phase-btn{padding:14px;background:var(--surface);border:1px solid var(--border);border-radius:8px;color:var(--text);font-family:'Orbitron',sans-serif;font-size:.7rem;letter-spacing:2px;cursor:pointer}
  .phase-btn.danger{border-color:var(--danger);color:var(--danger)}
  .phase-btn.ok{border-color:var(--ok);color:var(--ok)}

  .cat{background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden}
  .cat-header{padding:8px 12px;background:var(--surface2);font-family:'Orbitron',sans-serif;font-size:.65rem;color:var(--accent);border-bottom:1px solid var(--border)}
  .cat-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px;padding:6px}
  .abtn{background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-family:'Share Tech Mono',monospace;font-size:.62rem;padding:10px 4px;cursor:pointer;text-align:center;display:flex;flex-direction:column;align-items:center}

  .joystick-wrap{display:flex;flex-direction:column;align-items:center;gap:12px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:18px}
  .joystick{display:grid;grid-template-columns:80px 80px 80px;grid-template-rows:80px 80px 80px;gap:6px}
  .jbtn{background:var(--surface2);border:2px solid var(--border);border-radius:14px;color:var(--text);font-size:1.8rem;cursor:pointer;display:flex;align-items:center;justify-content:center}
  .jfwd{grid-column:2;grid-row:1} .jlft{grid-column:1;grid-row:2} .jstp{grid-column:2;grid-row:2;font-size:.7rem;color:var(--danger);} .jrgt{grid-column:3;grid-row:2} .jbwd{grid-column:2;grid-row:3}

  .tts-box{display:flex;flex-direction:column;gap:8px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px}
  .tts-input{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;color:var(--text);font-family:'Share Tech Mono',monospace;font-size:.95rem;width:100%;min-height:80px}
  .tts-send{padding:14px;background:rgba(0,212,255,.1);border:1px solid var(--accent);border-radius:6px;color:var(--accent);font-family:'Orbitron',sans-serif;font-size:.75rem;cursor:pointer}

  .mimic-box{display:flex;flex-direction:column;gap:10px;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px}
  .mimic-status{font-size:.7rem;text-align:center;padding:10px;border:1px solid var(--border);border-radius:6px;background:var(--surface2)}
  .mimic-status b{color:var(--accent)}
  .mimic-btns{display:grid;grid-template-columns:1fr 1fr;gap:8px}

  #emergencyBar{position:fixed;bottom:0;left:0;right:0;background:rgba(5,8,16,.95);border-top:2px solid var(--danger);padding:6px 10px;z-index:10}
  #stopBtn{width:100%;padding:14px;background:rgba(255,51,102,.18);border:2px solid var(--danger);border-radius:8px;color:var(--danger);font-family:'Orbitron',sans-serif;font-weight:900;cursor:pointer}
</style>
</head>
<body>

<header>
  <div class="logo">{{ n_robots }}-ROBOT SHOW</div>
  <div class="status-pill"><div class="dot" id="dot"></div><span id="stxt">CONNECTING</span></div>
</header>
<div class="fb" id="fb">— READY — Tap any button</div>

<div class="statusbar">
  <div>Robots: <span id="sbRobots">-</span></div>
  <div>Phase: <span id="sbPhase">-</span></div>
  <div>Pose Mimic: <span id="sbMimic">-</span></div>
  <div>Detected Pose: <span id="sbPose">-</span></div>
</div>

<div class="tabs">
  <button class="tab active" data-tab="phases" onclick="setTab('phases')">PHASES</button>
  <button class="tab" data-tab="actions" onclick="setTab('actions')">ACTIONS</button>
  <button class="tab" data-tab="move"    onclick="setTab('move')">MOVE</button>
  <button class="tab" data-tab="speak"   onclick="setTab('speak')">SPEAK</button>
  <button class="tab" data-tab="mimic"   onclick="setTab('mimic')">MIMIC</button>
</div>

<div class="panel active" id="panel-phases">
  <button class="phase-btn" onclick="startPhase('wave')">▶ PHASE 1 — WAVE</button>
  <button class="phase-btn" onclick="startPhase('march')">▶ PHASE 2 — MARCH</button>
  <button class="phase-btn" onclick="startPhase('dance')">▶ PHASE 3 — DANCE</button>
  <button class="phase-btn danger" onclick="startPhase('stop')">⏹ CANCEL CURRENT PHASE</button>
</div>

<div class="panel" id="panel-actions">
  {% for cat_id, cat_label, items in grouped %}
  <div class="cat">
    <div class="cat-header">{{cat_label}}</div>
    <div class="cat-grid">
      {% for key, spec in items %}
      <button class="abtn" onclick="fireAction('{{key}}')"><span class="ic">{{spec.icon}}</span><span>{{spec.label}}</span></button>
      {% endfor %}
    </div>
  </div>
  {% endfor %}
</div>

<div class="panel" id="panel-move">
  <div class="joystick-wrap">
    <div class="joystick">
      <button class="jbtn jfwd" data-dir="forward">▲</button>
      <button class="jbtn jlft" data-dir="left">◀</button>
      <button class="jbtn jstp" data-dir="stop">STOP</button>
      <button class="jbtn jrgt" data-dir="right">▶</button>
      <button class="jbtn jbwd" data-dir="backward">▼</button>
    </div>
  </div>
</div>

<div class="panel" id="panel-speak">
  <div class="tts-box">
    <textarea class="tts-input" id="ttsInput" placeholder="Type something..."></textarea>
    <button class="tts-send" onclick="sendTTS()">📢  SAY IT</button>
  </div>
</div>

<div class="panel" id="panel-mimic">
  <div class="mimic-box">
    <div class="mimic-status">
      Pose Mimic is <b id="mimicOn">-</b><br>
      Currently detected: <b id="mimicPose">-</b>
    </div>
    <div class="mimic-btns">
      <button class="phase-btn ok" onclick="poseStart()">▶ START MIMIC</button>
      <button class="phase-btn danger" onclick="poseStop()">⏹ STOP MIMIC</button>
    </div>
    {% if not pose_available %}
    <div class="fb warn">Pose mimic gating not available on this server.</div>
    {% else %}
    <div class="fb">Run <b>pose_client.py</b> on the laptop (separate venv) to open the camera window.</div>
    {% endif %}
  </div>
</div>

<div id="emergencyBar"><button id="stopBtn" onclick="emergencyStop()">🛑 RESET & IDLE</button></div>

<script>
let phaseIsRunning = false;

function setMsg(t,cls){ document.getElementById('fb').textContent=t; document.getElementById('fb').className='fb '+(cls||''); }
function setTab(n){ document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===n)); document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.id==='panel-'+n)); }

function lockUI(ms){
  const panels = document.querySelectorAll('.panel');
  panels.forEach(p => p.classList.add('locked'));
  setTimeout(() => { if(!phaseIsRunning) panels.forEach(p => p.classList.remove('locked')); }, ms);
}

async function checkStatus(){
  try{
    const d = await (await fetch('/status')).json();
    phaseIsRunning = d.phase_running;
    document.getElementById('dot').className = d.n_ready>0?'dot on':'dot err';
    document.getElementById('stxt').textContent = d.n_ready>0 ? d.n_ready+'/'+d.n_total+' ONLINE' : 'SEARCHING';
    document.getElementById('sbRobots').textContent = d.n_ready+'/'+d.n_total;
    document.getElementById('sbPhase').textContent = d.current_phase;

    if(phaseIsRunning){
      document.getElementById('stxt').textContent = '▶ ' + d.current_phase.toUpperCase();
      document.querySelectorAll('.panel').forEach(p => p.classList.add('locked'));
    } else {
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('locked'));
    }
  } catch(e){}

  try{
    const p = await (await fetch('/pose/status')).json();
    document.getElementById('sbMimic').textContent = p.available ? (p.enabled?'ON':'OFF') : 'N/A';
    document.getElementById('sbPose').textContent = p.available ? p.pose : '-';
    document.getElementById('mimicOn').textContent = p.available ? (p.enabled?'ON':'OFF') : 'UNAVAILABLE';
    document.getElementById('mimicPose').textContent = p.available ? p.pose : '-';
  } catch(e){}
}
setInterval(checkStatus, 1500); checkStatus();

async function startPhase(name){
  const r=await(await fetch('/phase/'+name,{method:'POST'})).json();
  setMsg(r.ok?'✓ '+r.message:'✗ '+r.error, r.ok?'ok':'err');
}

async function fireAction(k){
  const isDance = k.startsWith('dance_');
  lockUI(isDance ? 25000 : 3500);
  const r=await(await fetch('/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:k})})).json();
  setMsg(r.ok?'✓ '+r.message:'✗ '+r.error, r.ok?'ok':'err');
}

async function move(dir){
  lockUI(1500);
  const r=await(await fetch('/move',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({direction:dir})})).json();
  if(r.error) setMsg('✗ '+r.error, 'err');
}

async function sendTTS(){
  const text=document.getElementById('ttsInput').value.trim();
  if(!text)return;
  lockUI(3500);
  const r=await(await fetch('/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:text})})).json();
  if(r.ok){setMsg('📢 '+r.message,'ok');document.getElementById('ttsInput').value='';}
  else{setMsg('🚫 '+r.error,'err');}
}

async function poseStart(){
  const r = await (await fetch('/pose/start',{method:'POST'})).json();
  setMsg(r.ok?'✓ Pose mimic ON':'✗ '+(r.error||'error'), r.ok?'ok':'err');
}
async function poseStop(){
  const r = await (await fetch('/pose/stop',{method:'POST'})).json();
  setMsg(r.ok?'✓ Pose mimic OFF':'✗ '+(r.error||'error'), r.ok?'ok':'err');
}

async function emergencyStop(){
  const r=await(await fetch('/stop',{method:'POST'})).json();
  setMsg('🛑 '+r.message,'err');
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('locked'));
}

document.querySelectorAll('.jbtn').forEach(b => b.addEventListener('pointerdown', e => { e.preventDefault(); move(b.dataset.dir); }));
</script>
</body>
</html>
"""
