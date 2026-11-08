#!/usr/bin/env python3
"""Loopback-only Safari control surface for one M5 D&D model session."""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file


REPO = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("DND_CAMPAIGN_ROOT", str(Path.home() / ".claude/dnd"))).resolve()
RUNTIME = DATA_ROOT / ".runtime"
META_FILE = RUNTIME / "portal-session.json"
SESSION = os.environ.get("DND_PORTAL_TMUX_SESSION", "dnd-campaign-portal")
PORT = int(os.environ.get("DND_PORTAL_PORT", "5002"))
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ALLOWED_PROVIDERS = {"qwen", "codex", "claude"}
CODEX_ENABLED = os.environ.get("DND_PORTAL_ENABLE_CODEX", "1") == "1"


def run(*args: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{Path.home() / '.local/bin'}:/opt/homebrew/bin:{env.get('PATH', '')}"
    return subprocess.run(args, cwd=REPO, env=env, capture_output=True, text=True, timeout=timeout)


def running() -> bool:
    return run("tmux", "has-session", "-t", f"={SESSION}").returncode == 0


def read_meta() -> dict:
    try:
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def campaigns() -> list[str]:
    folder = DATA_ROOT / "campaigns"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_dir() and NAME_RE.fullmatch(p.name) and (p / "state.md").is_file())


def output() -> str:
    if not running():
        return ""
    result = run("tmux", "capture-pane", "-pt", f"{SESSION}:0.0", "-S", "-200")
    return result.stdout[-32000:] if result.returncode == 0 else ""


def notify_display(route: str, payload: dict) -> bool:
    try:
        payload = payload | {"campaign": read_meta().get("campaign")}
        token_file = RUNTIME / ".token"
        token = token_file.read_text(encoding="utf-8").strip() if token_file.is_file() else ""
        urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:5001" + route, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-DND-Token": token}, method="POST"), timeout=2).close()
        return True
    except OSError:
        return False


def notify_party_consumed(reply: str | None = None) -> None:
    notify_display("/queue/consumed", {"reply": reply} if reply else {})


def deliver_party_input() -> bool:
    if not running():
        return False
    meta = read_meta()
    queue = RUNTIME / ".input_queue"
    claim = RUNTIME / ".portal-input-claim"
    try:
        started_at = float(meta.get("started_at", 0))
    except (TypeError, ValueError):
        started_at = 0
    if claim.exists() and claim.stat().st_mtime < started_at:
        return False
    if not claim.exists():
        try:
            if queue.stat().st_mtime < started_at:
                return False
            queue.replace(claim)
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return False
    try:
        content = claim.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not content:
        claim.unlink(missing_ok=True)
        return False
    prompt = (
        "Party Input for the current DM turn. During campaign or character setup, treat it as "
        "the answer to your latest question; during play, treat it as the party's action. "
        "Send your next setup question to the cinematic display with send.py --setup-question before waiting. "
        "Input: " + json.dumps(content, ensure_ascii=True)
    )
    literal = run("tmux", "send-keys", "-t", f"{SESSION}:0.0", "-l", "--", prompt)
    if literal.returncode:
        return False
    time.sleep(0.25)
    enter = run("tmux", "send-keys", "-t", f"{SESSION}:0.0", "Enter")
    if enter.returncode:
        return False
    claim.unlink(missing_ok=True)
    notify_party_consumed()
    return True


def party_input_loop() -> None:
    stopped_session = None
    while True:
        try:
            active = running()
            if active:
                stopped_session = None
                deliver_party_input()
            else:
                meta = read_meta()
                session = (meta.get("campaign"), meta.get("started_at"))
                if session[0] and session != stopped_session:
                    if notify_display("/dm-status", {"status": "stopped"}):
                        stopped_session = session
        except Exception as error:
            print(f"Party Input bridge: {type(error).__name__}", flush=True)
        time.sleep(0.5)


def initial_prompt(mode: str, name: str, description: str) -> str:
    if mode == "new":
        return (
            f"Read this repository's D&D skill and begin the /dm:dnd new procedure for campaign {name}. "
            "The local cinematic display is already running at http://127.0.0.1:5001; reuse it without "
            "restarting the display or opening another tab. Ask each setup question in this interactive "
            "session and wait for my answer. Once the campaign is selected, send player-facing narration "
            "to the display using its existing scripts. Send every setup question to the cinematic "
            "display with send.py --setup-question before waiting; answers may arrive from the Party Input Everybody "
            "field and should be treated as setup answers until character creation is complete. "
            f"My initial campaign idea is: {description or 'Start a new campaign with me.'}"
        )
    return (
        f"Read this repository's D&D skill and follow the /dm:dnd load procedure for campaign {name}. "
        "Reconstruct state from the saved campaign files before narrating. The local browser display "
        "is already running at http://127.0.0.1:5001; reuse it without restarting or opening a tab. "
        "Send any setup question to the display with send.py --setup-question before waiting; Party Input Everybody "
        "answers the question. "
        f"My note for this session is: {description or 'Continue from where we stopped.'}"
    )


def model_args(provider: str, mode: str, name: str, description: str) -> list[str]:
    args = ["bash", str(REPO / "scripts/play-local.sh"), provider]
    if mode == "resume":
        args += ["--campaign", name]
    prompt = initial_prompt(mode, name, description)
    if provider == "codex":
        args += ["-m", "gpt-6.1-terra", "-c", 'model_reasoning_effort="medium"',
                 "-c", "sandbox_workspace_write.network_access=true",
                 "-s", "workspace-write",
                 "--add-dir", str(DATA_ROOT), prompt]
    elif provider == "qwen":
        args += ["-i", prompt]
    else:
        args += [prompt]
    return args


def prepare_display(name: str) -> None:
    base = "http://127.0.0.1:5001"
    with urllib.request.urlopen(base + "/health", timeout=5) as response:
        health = json.load(response)
    if health.get("data_root") != str(DATA_ROOT):
        raise ValueError("Display uses a different campaign data root")
    token_file = RUNTIME / ".token"
    token = token_file.read_text(encoding="utf-8").strip() if token_file.is_file() else ""
    headers = {"Content-Type": "application/json", "X-DND-Token": token}
    for route, payload in (("/clear", {}), ("/stats", {"campaign": name})):
        request = urllib.request.Request(base + route, data=json.dumps(payload).encode("utf-8"),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=5):
            pass
    with urllib.request.urlopen(base + "/health", timeout=5) as response:
        selected = json.load(response)
    expected = str(DATA_ROOT / "campaigns" / name / "session_tail.json")
    if selected.get("campaign") != name or selected.get("tail_path") != expected:
        raise ValueError("Display did not select the requested campaign")


def start_session(provider: str, mode: str, name: str, description: str) -> tuple[bool, str]:
    if provider not in ALLOWED_PROVIDERS or mode not in {"new", "resume"}:
        return False, "Choose a model and New or Resume."
    if provider == "codex" and not CODEX_ENABLED:
        return False, "Codex is disabled on this M5. Choose Qwen or remove the DND_PORTAL_ENABLE_CODEX=0 override."
    if not NAME_RE.fullmatch(name) or name in {".", ".."}:
        return False, "Campaign name must use letters, numbers, dots, dashes or underscores."
    if len(description) > 3000:
        return False, "Campaign description is too long."
    existing = name in campaigns()
    if mode == "new" and existing:
        return False, "That campaign already exists. Choose Resume or another name."
    if mode == "resume" and not existing:
        return False, "Saved campaign not found on the M5."
    if running():
        return False, "A model session is already running. Finish it before changing models or campaigns."
    try:
        if provider == "claude":
            auth = run("bash", str(REPO / "scripts/launch-campaign-m5.sh"), "--claude-auth")
            if auth.returncode:
                return False, "Claude Code is signed out on the M5. Sign in there or choose Qwen or Codex."
        service = run("env", "DND_PORTAL_START=1", "bash", str(REPO / "scripts/launch-campaign-m5.sh"), "--start", provider, timeout=60)
        if service.returncode:
            return False, (service.stderr or service.stdout or "M5 services did not start.").strip()[-800:]
        try:
            prepare_display(name)
        except (OSError, ValueError):
            return False, "Could not prepare a clean display for this campaign. No model session was started."
        path = f"{Path.home() / '.local/bin'}:/opt/homebrew/bin:{os.environ.get('PATH', '')}"
        command = shlex.join(["env", f"PATH={path}", f"DND_CAMPAIGN_ROOT={DATA_ROOT}",
                               f"DND_ACTIVE_CAMPAIGN={name}",
                               *model_args(provider, mode, name, description)])
        result = run("tmux", "new-session", "-d", "-s", SESSION, "-c", str(REPO), command)
        if result.returncode:
            return False, "Could not start the model session."
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, "The M5 model or service did not respond. Check the local service logs."
    RUNTIME.mkdir(parents=True, exist_ok=True)
    META_FILE.write_text(json.dumps({"provider": provider, "mode": mode, "campaign": name,
                                     "started_at": time.time()}), encoding="utf-8")
    notify_party_consumed()
    return True, "Model session started. Answer its setup questions below."


def create_app() -> Flask:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", SESSION):
        raise ValueError("Invalid DND_PORTAL_TMUX_SESSION")
    if not 1024 <= PORT <= 65535:
        raise ValueError("Invalid DND_PORTAL_PORT")
    app = Flask(__name__, template_folder=str(REPO / "skills/dnd/display/templates"))
    token = secrets.token_urlsafe(32)
    lock = threading.Lock()
    last_browser_poll: float | None = None

    @app.before_request
    def protect():
        if request.host not in {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}:
            return jsonify(error="Invalid host"), 403
        if request.path.startswith("/api/"):
            if request.headers.get("X-DND-Portal-Token") != token:
                return jsonify(error="Invalid session token"), 403
            if request.method == "POST" and request.headers.get("Origin") != f"http://{request.host}":
                return jsonify(error="Invalid origin"), 403

    @app.get("/")
    def home():
        return render_template("campaign_portal.html", token=token)

    @app.get("/icon.png")
    def icon():
        return send_file(REPO / "skills/dnd/display/icons/app_icon_192.png", mimetype="image/png")

    @app.get("/health")
    def health():
        return jsonify(service="dnd-campaign-portal", ready=True, data_root=str(DATA_ROOT),
                       browser_recent=last_browser_poll is not None and time.monotonic() - last_browser_poll < 5)

    @app.get("/api/state")
    def state():
        nonlocal last_browser_poll
        last_browser_poll = time.monotonic()
        active = running()
        return jsonify(active=active, meta=read_meta() if active else {}, output=output() if active else "",
                       campaigns=campaigns(), codex_enabled=CODEX_ENABLED)

    @app.post("/api/start")
    def start():
        data = request.get_json(silent=True) or {}
        provider = str(data.get("provider", ""))
        mode = str(data.get("mode", ""))
        name = str(data.get("name", "")).strip()
        description = str(data.get("description", "")).strip()
        if mode == "new" and not name:
            name = datetime.now().strftime("adventure-%Y%m%d-%H%M%S")
        with lock:
            ok, message = start_session(provider, mode, name, description)
        return jsonify(ok=ok, message=message, campaign=name if ok else None), 200 if ok else 409

    @app.post("/api/send")
    def send():
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", ""))
        if not text or len(text) > 3000 or any(c in text for c in "\r\n\x00"):
            return jsonify(ok=False, message="Enter one reply, up to 3000 characters."), 400
        with lock:
            if not running():
                return jsonify(ok=False, message="No model session is running."), 409
            literal = run("tmux", "send-keys", "-t", f"{SESSION}:0.0", "-l", "--", text)
            if literal.returncode == 0:
                time.sleep(0.25)
            enter = run("tmux", "send-keys", "-t", f"{SESSION}:0.0", "Enter") if literal.returncode == 0 else literal
        if enter.returncode:
            return jsonify(ok=False, message="Could not send the reply to the model."), 500
        notify_party_consumed(text)
        return jsonify(ok=True)

    return app


if __name__ == "__main__":
    threading.Thread(target=party_input_loop, daemon=True, name="dnd-party-input").start()
    create_app().run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
