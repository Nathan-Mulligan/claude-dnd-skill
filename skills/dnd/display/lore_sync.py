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
  * Only PUBLIC_SECTIONS of state.md reach the model (Active Quests, Open
    Threads, Recent Events, Party Knowledge). World State, Next Hooks,
    Faction Moves, Clocks, the Campaign Arc and other DM sections never do.
  * Notes are display-only, saved in the runtime dir (lore_notes.json).
    push_stats.py refills them when the DM re-pushes a list without notes.
  * Any failure is silent apart from runtime/lore_sync.log. A malformed
    model reply counts as a failure, so nothing is published.

Which model
  The campaign portal saves the choice in runtime/helper-settings.json, read
  on every run, so a change needs no DM restart. HELPERS lists the choices:
  off, luna (default, Codex CLI), terra (Codex CLI), haiku (Claude CLI) and
  qwen (local Ollama). Subscriptions and local models only, never an API key.
  Environment overrides win over the saved choice: DND_LORE_SYNC=0 switches
  the helper off; DND_LORE_MODEL names a choice above, or any other Codex
  model name. Codex runs from ~/.local/bin/codex when that exists, else from
  PATH.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from runtime_paths import rt  # noqa: E402

# Only sections that hold what the party knows. "World State" is left out on
# purpose: the state template keeps hidden faction activity there.
PUBLIC_SECTIONS = (
    "active quests",
    "open threads",
    "recent events",
    "party knowledge",
)
DETAIL_MAX = 320
LOCK_STALE_S = 600
MODEL_TIMEOUT_S = 180
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"

# Helper choices shown in the portal: key -> (provider, model, label).
HELPERS = {
    "off": (None, None, "Off"),
    "luna": ("codex", "gpt-6-luna", "GPT-6 Luna"),
    "terra": ("codex", "gpt-6.1-terra", "GPT-6.1 Terra"),
    "haiku": ("claude", "haiku", "Claude Haiku"),
    "qwen": ("ollama", "qwen3.6:35b-mlx", "Local Qwen"),
}
DEFAULT_HELPER = "luna"

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


def settings_path() -> str:
    return rt("helper-settings.json")


def saved_helper() -> str:
    """The helper chosen in the portal, or the default if none is saved."""
    try:
        with open(settings_path(), encoding="utf-8") as f:
            key = json.load(f).get("helper")
    except (OSError, ValueError, AttributeError):
        key = None
    return key if key in HELPERS else DEFAULT_HELPER


def save_helper(key: str) -> None:
    if key not in HELPERS:
        raise ValueError(f"unknown helper {key!r}")
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".helper-settings.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"helper": key}, f)
    os.replace(tmp, path)


def choose_model() -> "tuple[str, str] | None":
    """(provider, model) for this run, or None when the helper is off.
    DND_LORE_SYNC=0 and DND_LORE_MODEL win over the portal's saved choice."""
    if os.environ.get("DND_LORE_SYNC", "1").strip() == "0":
        return None
    override = os.environ.get("DND_LORE_MODEL", "").strip()
    if override:
        if override in HELPERS:
            provider, model, _ = HELPERS[override]
            return (provider, model) if provider else None
        return ("codex", override)  # any other name is a Codex model, as before
    provider, model, _ = HELPERS[saved_helper()]
    return (provider, model) if provider else None


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

def _load_all() -> dict:
    """{"campaigns": {name: entry}}; an entry is {campaign, fingerprint, factions, quests}."""
    try:
        with open(cache_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"campaigns": {}}
    if not isinstance(data, dict):
        return {"campaigns": {}}
    if "campaigns" not in data:  # single-campaign file from the first version
        name = data.get("campaign")
        return {"campaigns": {name: data} if name else {}}
    return data if isinstance(data.get("campaigns"), dict) else {"campaigns": {}}


def load_cache(campaign: "str | None") -> dict:
    """Saved notes for one campaign (kept per campaign, so switching back and
    forth never loses track of which notes this helper wrote)."""
    entry = _load_all()["campaigns"].get(campaign) if campaign else None
    return entry if isinstance(entry, dict) else {}


def save_cache(entry: dict) -> None:
    data = _load_all()
    data["campaigns"][entry["campaign"]] = entry
    path = cache_path()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".lore_notes.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)  # all campaigns
    os.replace(tmp, path)


def apply_cached(payload: dict, campaign: "str | None", cache: "dict | None" = None) -> dict:
    """Fill a missing `detail` on pushed factions/quests from saved notes.

    Notes are used only when `campaign` (the campaign this push is for) is
    known and matches the campaign they were written for. A detail the DM
    supplies itself always wins. Used by push_stats.py so a DM push without
    notes does not wipe the helper's notes from the screen.
    """
    cache = load_cache(campaign) if cache is None else cache
    if not campaign or cache.get("campaign") != campaign:
        return payload
    for key in ("factions", "quests"):
        notes = cache.get(key) or {}
        items = payload.get(key)
        if not notes or not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and not item.get("detail"):
                note = notes.get(str(item.get("name", "")))
                if note:
                    item["detail"] = note
    return payload


# ── display ──────────────────────────────────────────────────────────────────

def _display():
    """Return (base_url, ssl_context, token) using push_stats's settings."""
    import push_stats
    return push_stats.FLASK_URL.rsplit("/", 1)[0], push_stats._SSL_CTX, push_stats._read_token()


def _request(path: str, data: "bytes | None" = None, timeout: float = 5) -> "dict | None":
    base, ctx, token = _display()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-DND-Token"] = token
    req = urllib.request.Request(base + path, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        body = resp.read().decode("utf-8") or "{}"
    try:
        return json.loads(body)
    except ValueError:
        return {}


def _current_stats(tries: int = 5) -> "dict | None":
    """Read the display's saved stats. None means it could not be read cleanly
    (the display rewrites the file on every update), never an empty snapshot."""
    for i in range(tries):
        try:
            with open(rt("stats.json"), encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            pass
        time.sleep(0.25 * (i + 1))
    return None


def active_campaign() -> "str | None":
    """The campaign the display is showing now, or None if it can't be told."""
    try:
        return (_request("/health", timeout=2) or {}).get("campaign") or None
    except Exception:  # noqa: BLE001
        return None


# ── model ────────────────────────────────────────────────────────────────────

def build_prompt(public: str, factions: list, quests: list) -> str:
    def lines(xs, key):
        return "\n".join(f"- {x.get('name')} ({x.get(key) or 'unknown'})"
                         for x in xs if isinstance(x, dict) and x.get("name")) or "- none"
    return f"""You write short notes for a tabletop game's on-screen sidebar. Players read them.

For each faction and quest listed below, write one or two plain sentences saying what the
party knows about it, using ONLY the campaign notes given here. Never add facts, guesses,
motives or outcomes that the notes do not state. If the notes say nothing beyond the name,
return an empty string for that item. Use each name exactly as listed, without the bracketed
standing or status. Do not run commands.

FACTIONS (standing in brackets):
{lines(factions, "standing")}

QUESTS (status in brackets):
{lines(quests, "status")}

CAMPAIGN NOTES (party-known sections only):
{public or "(none)"}
"""


def _codex_bin() -> str:
    # The M5 also has an older Homebrew codex that rejects current models, so
    # the result must not depend on the PATH order of whoever started this run.
    local = Path.home() / ".local" / "bin" / "codex"
    return str(local) if local.is_file() and os.access(local, os.X_OK) else "codex"


def _tail(text: "str | None", prompt: str = "") -> str:
    """The last stderr line, and the one before it if both fit in 300 characters.
    Lines of the prompt are skipped, because codex echoes it."""
    echoed = {line.strip() for line in prompt.splitlines()}
    picked = []
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line or line in echoed:
            continue
        line = line[:300]
        if line in picked:
            continue
        if picked and len(line) + 3 + len(picked[0]) > 300:
            break
        picked.append(line)
        if len(picked) == 2:
            break
    return " | ".join(reversed(picked)) or "no error output"


def failure_reason(e: BaseException) -> str:
    """One short log line. Never the command, which carries the prompt."""
    if isinstance(e, subprocess.TimeoutExpired):
        return f"{Path(e.cmd[0]).name} timed out after {e.timeout:g}s"
    if isinstance(e, subprocess.CalledProcessError):
        return f"{Path(e.cmd[0]).name} exited {e.returncode}: {_tail(e.stderr)}"
    return f"{type(e).__name__}: {e}"[:300]


_API_KEY_VARS = ("CODEX_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def _ask_codex(prompt: str, model: str, schema: dict = _SCHEMA) -> object:
    with tempfile.TemporaryDirectory(prefix="lore_sync.") as work:
        schema_file = Path(work, "schema.json")
        out = Path(work, "out.json")
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        cmd = [_codex_bin(), "exec", "-m", model, "-c", 'model_reasoning_effort="low"',
               "--ignore-user-config", "--skip-git-repo-check", "--ephemeral", "--ignore-rules",
               "-s", "read-only", "-C", work, "--output-schema", str(schema_file),
               "-o", str(out), prompt]
        env = {k: v for k, v in os.environ.items() if k not in _API_KEY_VARS}  # subscription login only
        env["AGENT_TASK"] = "1"
        done = subprocess.run(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=MODEL_TIMEOUT_S, check=False)
        if done.returncode:
            raise RuntimeError(f"codex exited {done.returncode}: {_tail(done.stderr, prompt)}")
        return json.loads(out.read_text(encoding="utf-8"))


def _ask_claude(prompt: str, model: str, schema: dict = _SCHEMA) -> object:
    """Claude CLI in print mode on the user's subscription: no tools, no
    settings, no MCP servers, no saved session. ANTHROPIC_API_KEY is removed
    so the call can never be billed to an API account."""
    with tempfile.TemporaryDirectory(prefix="lore_sync.") as work:
        cmd = ["claude", "-p", "--model", model, "--output-format", "json",
               "--json-schema", json.dumps(schema), "--tools", "", "--setting-sources", "",
               "--strict-mcp-config", "--no-session-persistence",
               "--system-prompt", "You write short JSON notes for a tabletop game. Reply only with the JSON asked for."]
        env = {k: v for k, v in os.environ.items() if k not in _API_KEY_VARS}
        env["AGENT_TASK"] = "1"
        done = subprocess.run(cmd, cwd=work, env=env, input=prompt, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=MODEL_TIMEOUT_S, check=True)
    reply = json.loads(done.stdout)
    if not isinstance(reply, dict) or reply.get("is_error"):
        raise ValueError("claude reported an error")
    if "structured_output" in reply:
        return reply["structured_output"]
    return json.loads(reply.get("result") or "")


def _ask_ollama(prompt: str, model: str, schema: dict = _SCHEMA) -> object:
    body = {"model": model, "stream": False, "think": False, "format": schema,
            "options": {"temperature": 0.2},
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=MODEL_TIMEOUT_S) as resp:
        reply = json.loads(resp.read().decode("utf-8"))
    return json.loads(reply["message"]["content"])


_PROVIDERS = {"codex": _ask_codex, "claude": _ask_claude, "ollama": _ask_ollama}


def validate(result: object) -> dict:
    """Return the reply if it has exactly the notes shape; raise otherwise."""
    if not isinstance(result, dict) or set(result) != {"factions", "quests"}:
        raise ValueError("model reply is not {factions, quests}")
    for key in ("factions", "quests"):
        items = result[key]
        if not isinstance(items, list):
            raise ValueError(f"model reply {key} is not a list")
        for item in items:
            if (not isinstance(item, dict) or set(item) != {"name", "detail"}
                    or not all(isinstance(item[k], str) for k in ("name", "detail"))):
                raise ValueError(f"model reply has a malformed {key} item")
    return result


def ask(prompt: str, schema: dict, choice: "tuple[str, str] | None" = None) -> object:
    """Ask the chosen helper for JSON matching `schema`. Raises when the helper is off or fails."""
    choice = choice or choose_model()
    if choice is None:
        raise RuntimeError("helper is off")
    provider, model = choice
    return _PROVIDERS[provider](prompt, model, schema)


def ask_model(prompt: str, choice: "tuple[str, str] | None" = None) -> dict:
    """Ask the chosen helper for notes. Raises on any failure or malformed reply."""
    choice = choice or choose_model()
    if choice is None:
        raise RuntimeError("helper is off")
    provider, model = choice
    return validate(_PROVIDERS[provider](prompt, model))


def clean_notes(result: dict, names: list) -> dict:
    allowed = set(names)
    notes = {}
    for item in result if isinstance(result, list) else []:
        if not isinstance(item, dict):
            continue
        name, detail = str(item.get("name", "")), " ".join(str(item.get("detail", "")).split())
        if name not in allowed:  # some models copy the "(standing)" from the prompt
            name = re.sub(r"\s*\([^()]*\)\s*$", "", name)
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


def _pending_path() -> str:
    return rt("lore_sync.pending")


def _take_pending() -> bool:
    try:
        os.unlink(_pending_path())
        return True
    except OSError:
        return False


def _release(fd: int) -> None:
    os.close(fd)
    try:
        os.unlink(rt("lore_sync.lock"))
    except OSError:
        pass


def sync_once() -> str:
    """One pass: regenerate and publish notes if the inputs changed."""
    health = _request("/health") or {}
    campaign, root = health.get("campaign"), health.get("data_root")
    if not campaign or not root:
        return "no-campaign"
    state = Path(root, "campaigns", campaign, "state.md")
    public = public_text(state.read_text(encoding="utf-8")) if state.is_file() else ""
    stats = _current_stats()
    if stats is None:
        return "unreadable"
    factions, quests = stats.get("factions") or [], stats.get("quests") or []
    if not factions and not quests:
        return "empty"
    fp = fingerprint(public, factions, quests)
    old = load_cache(campaign)
    if old.get("fingerprint") == fp and old.get("published") is not False:
        return "unchanged"
    choice = choose_model()  # read each pass, so a portal change applies at once
    if choice is None:
        return "off"

    if old.get("fingerprint") == fp:  # notes already written, the screen never got them
        new = old
    else:
        result = ask_model(build_prompt(public, factions, quests), choice)
        new = {
            "campaign": campaign,
            "fingerprint": fp,
            "factions": clean_notes(result.get("factions"), [f.get("name") for f in factions if isinstance(f, dict)]),
            "quests": clean_notes(result.get("quests"), [q.get("name") for q in quests if isinstance(q, dict)]),
        }
    # Every note this helper has written for a name, so an older one still on
    # screen is never mistaken for a note the DM wrote.
    written = {k: {n: list(v) for n, v in ((old.get("written") or {}).get(k) or {}).items()} for k in ("factions", "quests")}
    for k in ("factions", "quests"):
        for n, note in (old.get(k) or {}).items():
            written[k].setdefault(n, [])
            if note not in written[k][n]:
                written[k][n] = (written[k][n] + [note])[-8:]
    new = dict(new, written=written, published=False)
    # Saved before publishing: a DM push in between already refills the new notes.
    save_cache(new)

    if active_campaign() != campaign:
        return "campaign-changed"
    body = {"campaign": campaign, "factions": new["factions"], "quests": new["quests"],
            "previous": written}
    try:
        _request("/stats/lore", json.dumps(body).encode("utf-8"))  # notes only; lists untouched
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return "campaign-changed"
        if e.code == 404:  # display older than /stats/lore: wait for its restart, never repost lists
            return "display-too-old"
        raise
    save_cache(dict(new, published=True))
    return "updated"


def run(settle_s: float = 2.0, max_passes: int = 3) -> str:
    """Sync until inputs stop changing. A run started while another is busy
    leaves a pending flag, so its refresh is never dropped."""
    fd = _acquire_lock()
    if fd is None:
        try:
            Path(_pending_path()).touch()
        except OSError:
            pass
        return "busy"
    outcomes = []
    try:
        _take_pending()
        time.sleep(settle_s)  # let the DM's push land first
        for i in range(max_passes):
            outcomes.append(sync_once())
            if not os.path.exists(_pending_path()):  # nothing new was pushed meanwhile
                break
            if i < max_passes - 1:  # at the limit, leave the flag for the successor run
                _take_pending()
        return ",".join(outcomes)
    finally:
        _release(fd)
        if os.path.exists(_pending_path()):  # pass limit reached with work queued
            kick()


def kick() -> None:
    """Start a sync pass in the background. Never raises, never blocks."""
    try:
        if choose_model() is None:
            return
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
            log("run failed: " + failure_reason(e))
    else:
        print(__doc__)
