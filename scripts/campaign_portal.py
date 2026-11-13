#!/usr/bin/env python3
"""Loopback-only Safari control surface for one M5 D&D model session."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arc_pulse  # noqa: E402


REPO = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("DND_CAMPAIGN_ROOT", str(Path.home() / ".claude/dnd"))).resolve()
RUNTIME = DATA_ROOT / ".runtime"
META_FILE = RUNTIME / "portal-session.json"
SESSION = os.environ.get("DND_PORTAL_TMUX_SESSION", "dnd-campaign-portal")
PORT = int(os.environ.get("DND_PORTAL_PORT", "5002"))
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ALLOWED_PROVIDERS = {"qwen", "codex", "claude"}
CODEX_ENABLED = os.environ.get("DND_PORTAL_ENABLE_CODEX", "1") == "1"
SESSION_LOCK = threading.RLock()
HANDOFF_TIMEOUT = 180
_handoff_lock = threading.Lock()
_handoff = {"status": "idle", "message": ""}
SCENE_PROTOCOL = (
    "Scene media: before narrating, read http://127.0.0.1:5001/health and its scene identity. "
    "When the actual place, activity or atmosphere changes meaningfully, bundle a scene cue with narration: "
    "send.py --music-event scene --music-setting <broad-setting> --music-mood <fitting-mood> "
    "--scene-key <stable-slug> --scene-label <short-name> --scene-description <specific-scenery-and-activity>. "
    "Use a new key even within the same broad setting; keep the existing key during continuation. "
    "Describe the actual view and musical atmosphere, including darkness where appropriate. "
    "Use --scene-era for the saved campaign world era, such as medieval, renaissance, ancient, modern, "
    "futuristic or a specific custom era. It guides image clothing, architecture and technology, not place mood. "
    "Omitted era inherits between authored scenes; undeclared fantasy defaults to medieval. Save the "
    "established era in existing world notes and keep explicitly established clockwork or magical constructs. "
    "Place tone is inherited through --scene-theme JSON: an outer place with mood, then child names "
    "with optional local mood overrides. Copy the current scene.theme path from health, append a child "
    "to enter it, or pop it on leaving; supply a new path for a new region. Omitted theme inherits; [] clears. "
    "Omit --music-mood to inherit. A cue mood temporarily overrides that scene; put lasting creepy-place "
    "overrides on the child theme node. Record stable place themes in existing world notes for revisits. "
    "Do not change scenes for a suggested destination, casual mention or every paragraph. "
)


def handoff_state() -> dict:
    with _handoff_lock:
        return dict(_handoff)


def handoff_busy() -> bool:
    return handoff_state()["status"] in {"saving", "starting"}


def set_handoff(status: str, message: str, **details) -> None:
    with _handoff_lock:
        _handoff.clear()
        _handoff.update(status=status, message=message, **details)



def run(*args: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{Path.home() / '.local/bin'}:/opt/homebrew/bin:{env.get('PATH', '')}"
    return subprocess.run(args, cwd=REPO, env=env, capture_output=True, text=True, timeout=timeout)


def running() -> bool:
    return run("tmux", "has-session", "-t", f"={SESSION}").returncode == 0


def read_meta() -> dict:
    try:
        data = json.loads(META_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
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
        payload = {"campaign": read_meta().get("campaign")} | payload
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
    with SESSION_LOCK:
        if handoff_busy():
            return False
        return _deliver_party_input()


def _deliver_party_input() -> bool:
    if not running():
        return False
    meta = read_meta()
    queue = RUNTIME / ".input_queue"
    claim = RUNTIME / ".portal-input-claim"
    try:
        started_at = float(meta.get("input_started_at", meta.get("started_at", 0)))
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
    try:
        campaign_dir = campaign_path(str(meta.get("campaign", "")))
        pulse = arc_pulse.plan(campaign_dir)
    except (OSError, ValueError):
        pulse = None
    prompt = (
        SCENE_PROTOCOL +
        "Party Input for the current DM turn. During campaign or character setup, treat it as "
        "the answer to your latest question; during play, treat it as the party's action. "
        "Send your next setup question to the cinematic display with send.py --setup-question before waiting. "
        "Input: " + json.dumps(content, ensure_ascii=True) +
        (" " + pulse.directive if pulse and pulse.directive else "")
    )
    literal = run("tmux", "send-keys", "-t", f"{SESSION}:0.0", "-l", "--", prompt)
    if literal.returncode:
        return False
    time.sleep(0.25)
    enter = run("tmux", "send-keys", "-t", f"{SESSION}:0.0", "Enter")
    if enter.returncode:
        return False
    claim.unlink(missing_ok=True)
    if pulse:
        try:
            arc_pulse.commit(campaign_dir, pulse)
        except OSError:
            pass
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


ONBOARDING_CHOICES = {
    "ruleset": ("2024", "2014"),
    "dice": ("players", "auto"),
    "tone": ("heroic", "cheerful", "grimdark", "dark fantasy", "horror", "political", "swashbuckling", "cosmic"),
    "magic": ("medium", "none", "low", "high"),
    "setting": ("medieval", "renaissance", "ancient", "nautical", "underground"),
    "danger": ("heroic", "gentle", "standard", "gritty", "lethal"),
    "story": ("guided", "sandbox"),
    "abilities": ("pointbuy",),
}
CHARACTER_LIMITS = {"name": 100, "player": 100, "concept": 1500, "details": 1500, "pillar": 500}


def read_onboarding(data: dict, mode: str) -> dict | None:
    if mode != "new" or "onboarding" not in data:
        return None
    value = data["onboarding"]
    allowed = set(ONBOARDING_CHOICES) | {"starting_level", "audience", "boundaries", "characters"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("Choose the new campaign questionnaire answers.")
    if len(json.dumps(value, ensure_ascii=False)) > 16000:
        raise ValueError("Questionnaire answers are too long. Shorten the character descriptions.")
    result = {}
    for key, choices in ONBOARDING_CHOICES.items():
        chosen = value.get(key, choices[0])
        if not isinstance(chosen, str) or chosen not in choices:
            raise ValueError(f"Choose a valid {key.replace('_', ' ')}.")
        result[key] = chosen
    level = value.get("starting_level", 1)
    if type(level) is not int or not 1 <= level <= 20:
        raise ValueError("Starting level must be a whole number from 1 to 20.")
    result["starting_level"] = level
    for key in ("audience", "boundaries"):
        text = value.get(key, "")
        if not isinstance(text, str) or len(text) > 1500:
            raise ValueError(f"Keep {key} to 1,500 characters.")
        result[key] = text.strip()
    characters = value.get("characters", [{}])
    if not isinstance(characters, list) or not 1 <= len(characters) <= 8:
        raise ValueError("Add between one and eight characters.")
    result["characters"] = []
    names = set()
    for character in characters:
        if not isinstance(character, dict) or set(character) - set(CHARACTER_LIMITS):
            raise ValueError("Each character needs its own questionnaire boxes.")
        entry = {}
        for key, limit in CHARACTER_LIMITS.items():
            text = character.get(key, "")
            if not isinstance(text, str) or len(text) > limit:
                raise ValueError(f"Character {key} must be text of at most {limit} characters.")
            entry[key] = text.strip()
        if entry["name"]:
            name = entry["name"].casefold()
            if name in names:
                raise ValueError("Give each character a different name, or leave the name blank for the DM.")
            names.add(name)
        result["characters"].append(entry)
    return result


def initial_prompt(mode: str, name: str, description: str, onboarding: dict | None = None) -> str:
    if mode == "new" and onboarding is not None:
        return (
            SCENE_PROTOCOL +
            f"Read this repository's D&D skill and complete /dm:dnd new for campaign {name}. "
            "The completed questionnaire below is the authoritative campaign and character setup. "
            "Submitting it authorises you to create all listed characters with legal, concept-fitting defaults "
            "for blank choices, balanced point-buy abilities, equipment and spells. Do not ask routine setup "
            "questions, repeat answered questions, or require character-build confirmation. Choose distinct "
            "names for unnamed characters. Respect the audience and content boundaries throughout. "
            "Guided story means generate a dynamic campaign arc; sandbox means no committed arc. "
            "Gentle danger means no killing, frightening scenes or permanent harm. "
            "Create and save canonical campaign files and complete character sheets, push the full party "
            "stats with --replace-players, clear any setup question, then begin the opening scene. "
            "Briefly explain necessary mechanical substitutions without delaying play. Normal player "
            "choices and dice prompts during actual play still apply. Do the model work yourself. "
            "The cinematic display is already running at http://127.0.0.1:5001; reuse it without "
            "restarting it or opening another tab. Send narration through its existing scripts. "
            f"Adventure idea: {description or 'Choose an adventure suited to these answers.'} "
            "Completed questionnaire JSON: " + json.dumps(onboarding, ensure_ascii=False)
        )
    if mode == "new":
        return (
            SCENE_PROTOCOL +
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
        SCENE_PROTOCOL +
        f"Read this repository's D&D skill and follow the /dm:dnd load procedure for campaign {name}. "
        "Reconstruct state from the saved campaign files before narrating. The local browser display "
        "is already running at http://127.0.0.1:5001; reuse it without restarting or opening a tab. "
        "If the display already contains narration for this campaign, keep it and its current scene without clearing or replaying it. "
        "If the display is empty, restore the saved scene. "
        "Send any setup question to the display with send.py --setup-question before waiting; Party Input Everybody "
        "answers the question. "
        f"My note for this session is: {description or 'Continue from where we stopped.'}"
    )


def model_args(provider: str, mode: str, name: str, description: str, onboarding: dict | None = None) -> list[str]:
    args = ["bash", str(REPO / "scripts/play-local.sh"), provider]
    if mode == "resume":
        args += ["--campaign", name]
    prompt = initial_prompt(mode, name, description, onboarding)
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


def prepare_display(name: str, reset: bool = True) -> None:
    base = "http://127.0.0.1:5001"
    with urllib.request.urlopen(base + "/health", timeout=5) as response:
        health = json.load(response)
    if health.get("data_root") != str(DATA_ROOT):
        raise ValueError("Display uses a different campaign data root")
    expected = str(DATA_ROOT / "campaigns" / name / "session_tail.json")
    if not reset and health.get("campaign") == name:
        if health.get("tail_path") != expected:
            raise ValueError("Display did not select the requested campaign")
        return
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
    if selected.get("campaign") != name or selected.get("tail_path") != expected:
        raise ValueError("Display did not select the requested campaign")


def selection_error(provider: str, mode: str, name: str, description: str) -> str | None:
    if provider not in ALLOWED_PROVIDERS or mode not in {"new", "resume"}:
        return "Choose a model and New or Resume."
    if provider == "codex" and not CODEX_ENABLED:
        return "Codex is disabled on this M5. Choose Qwen or remove the DND_PORTAL_ENABLE_CODEX=0 override."
    if not NAME_RE.fullmatch(name) or name in {".", ".."}:
        return "Campaign name must use letters, numbers, dots, dashes or underscores."
    try:
        campaign_path(name)
    except ValueError as error:
        return str(error)
    if len(description) > 3000:
        return "Campaign description is too long."
    existing = name in campaigns()
    if mode == "new" and existing:
        return "That campaign already exists. Choose Resume or another name."
    if mode == "resume" and not existing:
        return "Saved campaign not found on the M5."
    return None


def provider_error(provider: str) -> str | None:
    installed = run("bash", "-c", 'command -v "$1" >/dev/null', "dnd-provider-check", provider)
    if installed.returncode:
        return f"The selected DM ({provider}) is not installed. Choose another model."
    if provider == "claude":
        auth = run("bash", str(REPO / "scripts/launch-campaign-m5.sh"), "--claude-auth")
        if auth.returncode:
            return "Claude Code is signed out on the M5. Sign in there or choose Qwen or Codex."
    return None


def start_session(provider: str, mode: str, name: str, description: str, onboarding: dict | None = None) -> tuple[bool, str]:
    error = selection_error(provider, mode, name, description)
    if error:
        return False, error
    if running():
        return False, "A model session is already running. Finish it before changing models or campaigns."
    input_started_at = None
    previous = read_meta()
    if mode == "resume" and previous.get("campaign") == name:
        try:
            input_started_at = float(previous.get("input_started_at", previous["started_at"]))
            for pending in (RUNTIME / ".input_queue", RUNTIME / ".portal-input-claim"):
                if pending.is_symlink() or (pending.exists() and pending.stat().st_size and pending.stat().st_mtime < input_started_at):
                    return False, "An older reply cannot be assigned to this campaign safely. Your reply was kept; recover it before resuming."
        except (OSError, KeyError, TypeError, ValueError):
            return False, "A pending reply could not be verified. Your reply was kept; recover it before resuming."
    try:
        error = provider_error(provider)
        if error:
            return False, error
        service = run("env", "DND_PORTAL_START=1", "bash", str(REPO / "scripts/launch-campaign-m5.sh"), "--start", provider, timeout=60)
        if service.returncode:
            return False, (service.stderr or service.stdout or "M5 services did not start.").strip()[-800:]
        try:
            prepare_display(name, reset=mode == "new")
        except (OSError, ValueError):
            return False, "Could not prepare a clean display for this campaign. No model session was started."
        path = f"{Path.home() / '.local/bin'}:/opt/homebrew/bin:{os.environ.get('PATH', '')}"
        command = shlex.join(["env", f"PATH={path}", f"DND_CAMPAIGN_ROOT={DATA_ROOT}",
                               f"DND_ACTIVE_CAMPAIGN={name}",
                               *model_args(provider, mode, name, description, onboarding)])
        result = run("tmux", "new-session", "-d", "-s", SESSION, "-c", str(REPO), command)
        if result.returncode:
            return False, "Could not start the model session."
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, "The M5 model or service did not respond. Check the local service logs."
    RUNTIME.mkdir(parents=True, exist_ok=True)
    metadata = {"provider": provider, "mode": mode, "campaign": name, "started_at": time.time()}
    if input_started_at is not None:
        metadata["input_started_at"] = input_started_at
    META_FILE.write_text(json.dumps(metadata), encoding="utf-8")
    notify_party_consumed()
    if mode == "new" and onboarding is not None:
        return True, "Your DM is creating the campaign and characters from your answers. The story will appear here."
    return True, "Model session started. Answer its setup questions below."


def campaign_path(name: str) -> Path:
    if not NAME_RE.fullmatch(name) or name in {".", ".."}:
        raise ValueError("Invalid campaign name")
    folder = DATA_ROOT.resolve() / "campaigns" / name
    if folder.is_symlink() or folder.resolve().parent != DATA_ROOT.resolve() / "campaigns":
        raise ValueError("Campaign directory must stay inside the campaign data root")
    return folder


def handoff_paths(token: str) -> tuple[Path, Path]:
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("Invalid handoff token")
    folder = RUNTIME / "handoffs"
    if RUNTIME.is_symlink() or folder.is_symlink():
        raise ValueError("Handoff directory cannot be a symbolic link")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.resolve().parent != RUNTIME.resolve():
        raise ValueError("Handoff directory is outside the display runtime")
    paths = (folder / f"{token}.request.json", folder / f"{token}.ack.json")
    if any(path.is_symlink() for path in paths):
        raise ValueError("Handoff files cannot be symbolic links")
    return paths


def write_private_json(path: Path, data: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())


def session_identity(meta: dict) -> tuple:
    return tuple(meta.get(key) for key in ("campaign", "provider", "started_at"))


def tmux_identity() -> str:
    result = run("tmux", "display-message", "-p", "-t", f"={SESSION}:", "#{session_id}")
    identity = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"\$[0-9]+", identity):
        raise ValueError("Could not identify the active DM session")
    return identity


def checkpoint_files(name: str, requested_at_ns: int) -> dict:
    folder = campaign_path(name)
    required = {"state.md", "session-log.md"}
    paths = list(folder.glob("*.md")) + list((folder / "characters").glob("*.md"))
    if (folder / "graph.json").exists():
        paths.append(folder / "graph.json")
    files = {}
    for path in paths:
        relative = path.relative_to(folder).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(folder):
            raise ValueError("Checkpoint files must stay inside their campaign")
        before = path.stat()
        if relative in required and (before.st_size == 0 or before.st_mtime_ns < requested_at_ns):
            raise ValueError("The DM has not saved a fresh state and session log yet")
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise ValueError("Campaign files are still changing; wait for the save to finish")
        files[relative] = hasher.hexdigest()
    if not required.issubset(files):
        raise ValueError("The campaign needs a saved state and session log before changing models")
    return files


def read_handoff_json(path: Path) -> dict:
    if (RUNTIME.is_symlink() or path.parent.is_symlink() or path.is_symlink() or
            path.parent.resolve().parent != RUNTIME.resolve() or path.stat().st_size > 131072):
        raise ValueError("Invalid handoff file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid handoff data")
    return data


def acknowledge_handoff(token: str) -> None:
    request_path, ack_path = handoff_paths(token)
    pending = read_handoff_json(request_path)
    if pending.get("token") != token or session_identity(read_meta()) != tuple(pending.get("session", [])):
        raise ValueError("The active campaign changed before this checkpoint")
    name = pending.get("campaign", "")
    files = checkpoint_files(name, pending["requested_at_ns"])
    temporary = ack_path.with_suffix(".writing")
    written = False
    try:
        write_private_json(temporary, {"token": token, "campaign": name, "complete": True, "files": files})
        written = True
        if ack_path.exists() or ack_path.is_symlink():
            raise ValueError("This checkpoint already has an acknowledgement")
        temporary.replace(ack_path)
    finally:
        if written and temporary.exists() and not temporary.is_symlink():
            temporary.unlink()


def send_checkpoint_request(prompt: str) -> None:
    literal = run("tmux", "send-keys", "-t", f"{SESSION}:0.0", "-l", "--", prompt)
    if literal.returncode:
        raise ValueError("Could not ask the current DM to save")
    time.sleep(0.25)
    probe = run("tmux", "capture-pane", "-pt", f"{SESSION}:0.0", "-S", "-80")
    visible = re.sub(r"\s+", "", probe.stdout)
    tail = re.sub(r"\s+", "", prompt[-160:])
    if probe.returncode or (tail not in visible and not re.search(r"pasted (?:text|content)", probe.stdout, re.I)):
        raise ValueError("The save request was not visible in the DM composer; it was not submitted")
    enter = run("tmux", "send-keys", "-t", f"{SESSION}:0.0", "Enter")
    if enter.returncode:
        raise ValueError("The save request could not be submitted")


def queued_party_input() -> bool:
    for path in (RUNTIME / ".input_queue", RUNTIME / ".portal-input-claim"):
        try:
            if path.stat().st_size:
                return True
        except FileNotFoundError:
            pass
    return False


def perform_handoff(provider: str, mode: str, name: str, description: str,
                    previous: dict, identity: str, token: str, onboarding: dict | None = None) -> None:
    stopped = False
    leased = False
    request_path = ack_path = None
    try:
        old_campaign = previous["campaign"]
        old_path = campaign_path(old_campaign)
        request_path, ack_path = handoff_paths(token)
        requested_at_ns = time.time_ns()
        pending = {"token": token, "campaign": old_campaign, "session": list(session_identity(previous)),
                   "requested_at_ns": requested_at_ns}
        write_private_json(request_path, pending)
        command = shlex.join(["env", f"DND_CAMPAIGN_ROOT={DATA_ROOT}", sys.executable,
                              str(REPO / "scripts/campaign_portal.py"), "--acknowledge-handoff", token])
        prompt = (
            f"Finish your response, then quietly save campaign {old_campaign} in {old_path}. "
            "Checkpoint state.md, session-log.md, all character sheets and graph using the D&D save procedure. "
            "Rewrite state.md and session-log.md even if unchanged. Do not narrate or ask a question. "
            f"After every save finishes, run {command}. If saving fails, do not acknowledge. "
            "Then stop generating and wait for the portal to change models."
        )
        with SESSION_LOCK:
            if session_identity(read_meta()) != session_identity(previous) or tmux_identity() != identity:
                raise ValueError("The active DM changed before saving began")
            error = selection_error(provider, mode, name, description) or provider_error(provider)
            if error:
                raise ValueError(error)
            if not notify_display("/campaign-handoff", {"campaign": old_campaign, "token": token, "active": True}):
                raise ValueError("The display has pending replies or could not pause input safely")
            leased = True
            send_checkpoint_request(prompt)
        deadline = time.monotonic() + HANDOFF_TIMEOUT
        while time.monotonic() < deadline:
            if session_identity(read_meta()) != session_identity(previous) or not running() or tmux_identity() != identity:
                raise ValueError("The active DM session changed before its save was confirmed")
            if ack_path.exists():
                ack = read_handoff_json(ack_path)
                if ack.get("token") != token or ack.get("campaign") != old_campaign or ack.get("complete") is not True:
                    raise ValueError("The save acknowledgement did not match this campaign")
                if ack.get("files") != checkpoint_files(old_campaign, requested_at_ns):
                    raise ValueError("Campaign files changed after the checkpoint acknowledgement")
                break
            time.sleep(0.25)
        else:
            raise ValueError("The DM did not confirm a complete save in time")
        with SESSION_LOCK:
            if session_identity(read_meta()) != session_identity(previous) or tmux_identity() != identity:
                raise ValueError("The active DM changed before the model switch")
            if queued_party_input():
                raise ValueError("A party reply arrived while saving. Let the current DM answer it before changing models")
            error = selection_error(provider, mode, name, description) or provider_error(provider)
            if error:
                raise ValueError(error)
            if not notify_display("/campaign-handoff", {"campaign": old_campaign, "token": token, "active": True}):
                raise ValueError("Could not keep player input paused safely for the model change")
            if ack.get("files") != checkpoint_files(old_campaign, requested_at_ns):
                raise ValueError("Campaign files changed before the model switch. Save again before changing DMs")
            set_handoff("starting", "Campaign saved. Starting the selected DM…", campaign=name, provider=provider)
            result = run("tmux", "kill-session", "-t", identity)
            if result.returncode:
                raise ValueError("Could not close the saved DM session")
            stopped = True
            if running():
                raise ValueError("Another DM session is active; the replacement was not started")
            ok, message = start_session(provider, mode, name, description, onboarding)
            if not ok:
                raise ValueError(message)
        set_handoff("complete", "Campaign saved and the selected DM is ready.", campaign=name, provider=provider)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        suffix = (" Your campaign is saved. Reopen it to try the new DM again." if stopped else
                  " The original DM was not stopped.")
        set_handoff("failed", str(error) + suffix, campaign=name, provider=provider)
    finally:
        if leased:
            notify_display("/campaign-handoff", {"campaign": previous["campaign"], "token": token, "active": False})
        try:
            owned_paths = handoff_paths(token)
        except (OSError, ValueError):
            owned_paths = ()
        for path in (request_path, ack_path):
            if path in owned_paths and not path.is_symlink():
                path.unlink(missing_ok=True)


def create_app() -> Flask:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", SESSION):
        raise ValueError("Invalid DND_PORTAL_TMUX_SESSION")
    if not 1024 <= PORT <= 65535:
        raise ValueError("Invalid DND_PORTAL_PORT")
    app = Flask(__name__, template_folder=str(REPO / "skills/dnd/display/templates"))
    app.config["MAX_CONTENT_LENGTH"] = 65536
    token = secrets.token_urlsafe(32)
    lock = SESSION_LOCK
    last_browser_poll: float | None = None

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(ok=False, message="Campaign answers are too long. Shorten the descriptions."), 413

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
                       campaigns=campaigns(), codex_enabled=CODEX_ENABLED, handoff=handoff_state())

    @app.post("/api/start")
    def start():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(ok=False, message="Choose a campaign and model."), 400
        provider = str(data.get("provider", ""))
        mode = str(data.get("mode", ""))
        name = str(data.get("name", "")).strip()
        description = str(data.get("description", "")).strip()
        try:
            onboarding = read_onboarding(data, mode)
        except ValueError as error:
            return jsonify(ok=False, message=str(error)), 400
        if mode == "new" and not name:
            name = datetime.now().strftime("adventure-%Y%m%d-%H%M%S")
        with lock:
            if handoff_busy():
                return jsonify(ok=False, message="The current campaign is being saved. Wait for the model change to finish."), 409
            ok, message = start_session(provider, mode, name, description, onboarding)
        return jsonify(ok=ok, message=message, campaign=name if ok else None), 200 if ok else 409

    @app.post("/api/switch")
    def switch():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(ok=False, message="Choose a campaign and model."), 400
        provider = str(data.get("provider", ""))
        mode = str(data.get("mode", ""))
        name = str(data.get("name", "")).strip()
        description = str(data.get("description", "")).strip()
        try:
            onboarding = read_onboarding(data, mode)
        except ValueError as error:
            return jsonify(ok=False, message=str(error)), 400
        if mode == "new" and not name:
            name = datetime.now().strftime("adventure-%Y%m%d-%H%M%S")
        with lock:
            if handoff_busy():
                return jsonify(ok=False, message="A campaign save and model change is already in progress."), 409
            error = selection_error(provider, mode, name, description)
            if error:
                return jsonify(ok=False, message=error), 409
            if not running():
                ok, message = start_session(provider, mode, name, description, onboarding)
                if ok:
                    set_handoff("complete", message, campaign=name, provider=provider)
                return jsonify(ok=ok, message=message, campaign=name if ok else None), 200 if ok else 409
            previous = read_meta()
            if mode == "new" and previous.get("campaign") == name:
                return jsonify(ok=False, message="This campaign is already in use. Choose Resume to change its DM, or choose a new name."), 409
            if mode == "resume" and previous.get("campaign") == name and previous.get("provider") == provider:
                return jsonify(ok=True, message="The selected DM is already running this campaign.", campaign=name,
                               continuing=True), 200
            try:
                old_campaign = previous.get("campaign")
                if not isinstance(old_campaign, str) or previous.get("provider") not in ALLOWED_PROVIDERS:
                    raise ValueError("Unknown active campaign")
                campaign_path(old_campaign)
                identity = tmux_identity()
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return jsonify(ok=False, message="Could not identify the current campaign safely. Its DM was left running."), 409
            if queued_party_input():
                return jsonify(ok=False, message="A party reply is still queued. Let the current DM answer it before changing models."), 409
            try:
                error = provider_error(provider)
            except (OSError, subprocess.TimeoutExpired):
                error = "Could not check whether the selected DM is available. The current DM was left running."
            if error:
                return jsonify(ok=False, message=error), 409
            token = secrets.token_hex(16)
            set_handoff("saving", "Saving the current campaign before changing DMs…", campaign=name, provider=provider)
            worker = threading.Thread(target=perform_handoff,
                                      args=(provider, mode, name, description, previous, identity, token, onboarding),
                                      daemon=True, name="dnd-model-handoff")
            worker.start()
        return jsonify(ok=True, message="Saving the current campaign before changing DMs…", campaign=name), 202

    @app.post("/api/send")
    def send():
        data = request.get_json(silent=True) or {}
        text = str(data.get("text", ""))
        if not text or len(text) > 3000 or any(c in text for c in "\r\n\x00"):
            return jsonify(ok=False, message="Enter one reply, up to 3000 characters."), 400
        with lock:
            if handoff_busy():
                return jsonify(ok=False, message="The campaign is being saved. Your reply was not sent; wait for the model change."), 409
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-handoff", metavar="TOKEN")
    args = parser.parse_args()
    if args.acknowledge_handoff:
        try:
            acknowledge_handoff(args.acknowledge_handoff)
        except (OSError, ValueError) as error:
            raise SystemExit(f"Checkpoint not acknowledged: {error}")
        print("Campaign checkpoint verified and acknowledged.")
        raise SystemExit(0)
    threading.Thread(target=party_input_loop, daemon=True, name="dnd-party-input").start()
    create_app().run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
