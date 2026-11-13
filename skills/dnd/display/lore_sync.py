#!/usr/bin/env python3
"""lore_sync.py — background notes for the sidebar's factions and quests.

When a player clicks a faction or quest in the display sidebar, the card shows
the item's optional `detail` text. This helper writes those notes so the DM
does not have to.

How it runs
  push_stats.py calls `kick()` whenever the DM pushes --factions or --quests.
  `kick()` starts this file detached and returns at once; the DM never waits.
  The run fingerprints the displayed names and statuses plus the party-known
  sections of the campaign's state.md. It calls the model only when that
  fingerprint has changed, so ordinary narration costs nothing.

Rules
  * Reads campaign files; never writes them. The DM stays the only writer.
  * Only PUBLIC_SECTIONS of state.md reach the model. Next Hooks, Faction
    Moves, Clocks, the Campaign Arc and other DM-only sections never do.
  * Notes are display-only, saved in the runtime dir (lore_notes.json).
    push_stats.py refills them when the DM re-pushes a list without notes.
  * Any failure is silent apart from runtime/lore_sync.log. Set
    DND_LORE_SYNC=0 to switch the helper off; DND_LORE_MODEL picks the model
    (default gpt-6-luna, run through the Codex CLI on the user's subscription).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from runtime_paths import rt  # noqa: E402

PUBLIC_SECTIONS = (
    "world state",
    "active quests",
    "open threads",
    "recent events",
    "party knowledge",
)
DETAIL_MAX = 320
LOCK_STALE_S = 600
MODEL_TIMEOUT_S = 180

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["factions", "quests"],
    "properties": {
        key: {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "detail"],
                "properties": {"name": {"type": "string"}, "detail": {"type": "string"}},
            },
        }
        for key in ("factions", "quests")
    },
}


def cache_path() -> str:
    return rt("lore_notes.json")


def log(msg: str) -> None:
    try:
        with open(rt("lore_sync.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ── state.md ─────────────────────────────────────────────────────────────────

def public_text(state_md: str) -> str:
    """Return only the party-known sections of state.md, headings included."""
    out, keep = [], False
    for line in state_md.splitlines():
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            title = m.group(1).strip().lower()
            keep = any(title.startswith(s) for s in PUBLIC_SECTIONS)
        elif line.startswith("# "):
            keep = False
        if keep:
            out.append(line)
    return "\n".join(out).strip()


def fingerprint(public: str, factions: list, quests: list) -> str:
    def items(xs, key):
        return [[str(x.get("name", "")), str(x.get(key, ""))] for x in xs if isinstance(x, dict)]
    blob = json.dumps([public, items(factions, "standing"), items(quests, "status")], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── cache ────────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    try:
        with open(cache_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(data: dict) -> None:
    path = cache_path()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".lore_notes.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def apply_cached(payload: dict, cache: "dict | None" = None) -> dict:
    """Fill a missing `detail` on pushed factions/quests from saved notes.

    A detail the DM supplies itself always wins. Used by push_stats.py so a DM
    push without notes does not wipe the helper's notes from the screen.
    """
    cache = load_cache() if cache is None else cache
    campaign = cache.get("campaign")
    for key in ("factions", "quests"):
        notes = cache.get(key) or {}
        items = payload.get(key)
        if not notes or not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and not item.get("detail"):
                note = notes.get(str(item.get("name", "")))
                if note and (not campaign or not payload.get("campaign") or payload["campaign"] == campaign):
                    item["detail"] = note
    return payload


# ── display ──────────────────────────────────────────────────────────────────

def _display():
    """Return (base_url, ssl_context, token) using push_stats's settings."""
    import push_stats
    return push_stats.FLASK_URL.rsplit("/", 1)[0], push_stats._SSL_CTX, push_stats._read_token()


def _request(path: str, data: "bytes | None" = None) -> "dict | None":
    base, ctx, token = _display()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-DND-Token"] = token
    req = urllib.request.Request(base + path, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
        body = resp.read().decode("utf-8") or "{}"
    try:
        return json.loads(body)
    except ValueError:
        return {}


def _current_stats() -> dict:
    try:
        with open(rt("stats.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# ── model ────────────────────────────────────────────────────────────────────

def build_prompt(public: str, factions: list, quests: list) -> str:
    def lines(xs, key):
        return "\n".join(f"- {x.get('name')} ({x.get(key) or 'unknown'})"
                         for x in xs if isinstance(x, dict) and x.get("name")) or "- none"
    return f"""You write short notes for a tabletop game's on-screen sidebar. Players read them.

For each faction and quest listed below, write one or two plain sentences saying what the
party knows about it, using ONLY the campaign notes given here. Never add facts, guesses,
motives or outcomes that the notes do not state. If the notes say nothing beyond the name,
return an empty string for that item. Use each name exactly as listed. Do not run commands.

FACTIONS (standing in brackets):
{lines(factions, "standing")}

QUESTS (status in brackets):
{lines(quests, "status")}

CAMPAIGN NOTES (party-known sections only):
{public or "(none)"}
"""


def ask_model(prompt: str) -> dict:
    model = os.environ.get("DND_LORE_MODEL", "gpt-6-luna").strip() or "gpt-6-luna"
    with tempfile.TemporaryDirectory(prefix="lore_sync.") as work:
        schema = Path(work, "schema.json")
        out = Path(work, "out.json")
        schema.write_text(json.dumps(_SCHEMA), encoding="utf-8")
        cmd = ["codex", "exec", "-m", model, "-c", 'model_reasoning_effort="low"',
               "--ignore-user-config", "--skip-git-repo-check", "--ephemeral", "--ignore-rules",
               "-s", "read-only", "-C", work, "--output-schema", str(schema),
               "-o", str(out), prompt]
        env = dict(os.environ, AGENT_TASK="1")
        subprocess.run(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=MODEL_TIMEOUT_S, check=True)
        return json.loads(out.read_text(encoding="utf-8"))


def clean_notes(result: dict, names: list) -> dict:
    allowed = set(names)
    notes = {}
    for item in result if isinstance(result, list) else []:
        if not isinstance(item, dict):
            continue
        name, detail = str(item.get("name", "")), " ".join(str(item.get("detail", "")).split())
        if name in allowed and detail:
            notes[name] = detail[:DETAIL_MAX]
    return notes


# ── run ──────────────────────────────────────────────────────────────────────

def _acquire_lock() -> "int | None":
    lock = rt("lore_sync.lock")
    try:
        if time.time() - os.path.getmtime(lock) > LOCK_STALE_S:
            os.unlink(lock)
    except OSError:
        pass
    try:
        return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None


def run(settle_s: float = 2.0) -> str:
    """One sync pass. Returns a short outcome word (for the log and tests)."""
    fd = _acquire_lock()
    if fd is None:
        return "busy"
    try:
        time.sleep(settle_s)  # let the DM's push land first
        health = _request("/health") or {}
        campaign, root = health.get("campaign"), health.get("data_root")
        if not campaign or not root:
            return "no-campaign"
        state = Path(root, "campaigns", campaign, "state.md")
        public = public_text(state.read_text(encoding="utf-8")) if state.is_file() else ""
        stats = _current_stats()
        factions, quests = stats.get("factions") or [], stats.get("quests") or []
        if not factions and not quests:
            return "empty"
        fp = fingerprint(public, factions, quests)
        cache = load_cache()
        if cache.get("fingerprint") == fp and cache.get("campaign") == campaign:
            return "unchanged"
        result = ask_model(build_prompt(public, factions, quests))
        old = cache
        cache = {
            "campaign": campaign,
            "fingerprint": fp,
            "factions": clean_notes(result.get("factions"), [f.get("name") for f in factions if isinstance(f, dict)]),
            "quests": clean_notes(result.get("quests"), [q.get("name") for q in quests if isinstance(q, dict)]),
        }
        save_cache(cache)
        # Re-read the latest lists so a DM push made meanwhile is kept as sent.
        latest = _current_stats()
        payload = {k: [dict(x) for x in (latest.get(k) or []) if isinstance(x, dict)] for k in ("factions", "quests")}
        # Drop only notes this helper wrote earlier; a DM-written detail stays.
        for k in ("factions", "quests"):
            mine = old.get(k) or {}
            for x in payload[k]:
                if x.get("detail") and mine.get(str(x.get("name", ""))) == x["detail"]:
                    x.pop("detail")
        apply_cached(payload, cache)
        _request("/stats", json.dumps(payload).encode("utf-8"))
        return "updated"
    finally:
        os.close(fd)
        try:
            os.unlink(rt("lore_sync.lock"))
        except OSError:
            pass


def kick() -> None:
    """Start a sync pass in the background. Never raises, never blocks."""
    if os.environ.get("DND_LORE_SYNC", "1").strip() == "0":
        return
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--run"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except Exception as e:  # noqa: BLE001 — must never break the DM's push
        log(f"kick failed: {e}")


if __name__ == "__main__":
    if "--run" in sys.argv:
        try:
            log("run: " + run())
        except Exception as e:  # noqa: BLE001
            log(f"run failed: {type(e).__name__}: {e}")
    else:
        print(__doc__)
