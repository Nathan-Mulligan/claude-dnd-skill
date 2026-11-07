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


def initial_prompt(mode: str, name: str, description: str) -> str:
    if mode == "new":
        return (
            f"Read this repository's D&D skill and begin the /dm:dnd new procedure for campaign {name}. "
            "The local cinematic display is already running at http://127.0.0.1:5001; reuse it without "
            "restarting the display or opening another tab. Ask each setup question in this interactive "
            "session and wait for my answer. Once the campaign is selected, send player-facing narration "
            "to the display using its existing scripts. "
            f"My initial campaign idea is: {description or 'Start a new campaign with me.'}"
        )
    return (
        f"Read this repository's D&D skill and follow the /dm:dnd load procedure for campaign {name}. "
        "Reconstruct state from the saved campaign files before narrating. The local browser display "
        "is already running at http://127.0.0.1:5001; reuse it without restarting or opening a tab. "
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
        path = f"{Path.home() / '.local/bin'}:/opt/homebrew/bin:{os.environ.get('PATH', '')}"
        command = shlex.join(["env", f"PATH={path}", f"DND_CAMPAIGN_ROOT={DATA_ROOT}",
                               *model_args(provider, mode, name, description)])
        result = run("tmux", "new-session", "-d", "-s", SESSION, "-c", str(REPO), command)
        if result.returncode:
            return False, "Could not start the model session."
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, "The M5 model or service did not respond. Check the local service logs."
    RUNTIME.mkdir(parents=True, exist_ok=True)
    META_FILE.write_text(json.dumps({"provider": provider, "mode": mode, "campaign": name}), encoding="utf-8")
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
        return jsonify(ok=True)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
