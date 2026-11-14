#!/usr/bin/env python3
"""
send.py — send text to the DnD DM display server.

Usage:
    # DM narration (default)
    python3 send.py << 'DNDEND'
    The tavern reeks of old ale and burnt tallow.
    DNDEND

    # Player action — prepends character name on display
    python3 send.py --player Flerb << 'DNDEND'
    Flerb draws her greatsword and steps forward.
    DNDEND

    # Dice result — pipe from dice.py for open rolls
    python3 ../scripts/dice.py d20+4 | python3 send.py --dice

    # NPC dialogue — amber border, italic, amber name header
    python3 send.py --npc "Vesna" << 'DNDEND'
    "I've been waiting for you."
    DNDEND

    # A new speaking NPC: give them a voice the first time they speak
    python3 send.py --npc "Vesna" --npc-voice bf_alice << 'DNDEND'
    "I've been waiting for you."
    DNDEND

    # Tutor/learning mode hint — collapsible parchment block on display
    python3 send.py --tutor << 'DNDEND'
    You could try a Perception check (WIS) to scan the room before acting.
    DNDEND

    # Player action intent — subdued label echoing what the player declared
    python3 send.py --action "Bob" << 'DNDEND'
    Attempts to shimmy across the rope to the ship under cover of darkness.
    DNDEND

    # Scene change, bundled with the narration that enters it
    python3 send.py --scene "Saltmere, Meren's bakery: haggling over flour by the ovens" << 'DNDEND'
    Warm bread and woodsmoke fill the little shop.
    DNDEND

    # A whole turn in one call: a line holding only @player NAME, @npc NAME [voice=ID],
    # @dice, @tutor, @action NAME or @narration starts a block; text before the first marker
    # is narration. Scene, mood, music and stat flags apply to the whole call.
    python3 send.py --scene "Brackenford: the toll gate at dusk" << 'DNDEND'
    @player Everybody
    We hand over the satchel.
    @narration
    The clerk breaks the seal.
    @npc Hessa Varn voice=bf_alice
    "Twenty silver, or the wagon stays."
    DNDEND

    # A roll in the same call: the narration naming the DC goes first, then the request
    python3 send.py --dice-request --character Nella --modifier 3 --dc 13 --label "Persuasion" --wait << 'DNDEND'
    Hessa folds her arms. DC 13; fail and the price holds.
    DNDEND

    # Short inline string
    echo "Short message" | python3 send.py

    # Campaign or character setup question (shown beside Party Input, not in story history)
    echo "What is your character's name?" | python3 send.py --setup-question

    # State changes bundled with narration (Option B)
    python3 send.py --stat-hp "Mira:12:17" --stat-slot-use "Aldric:1" << 'DNDEND'
    The goblin's blade finds a gap in her armor for 5 damage...
    DNDEND

    # Supported stat flags (can repeat for multiple players):
    #   --stat-hp         "NAME:CURRENT:MAX"
    #   --stat-temp-hp    "NAME:N"
    #   --stat-slot-use   "NAME:LEVEL"       (expend one slot)
    #   --stat-slot-restore "NAME:LEVEL"     (restore one slot)
    #   --stat-condition-add    "NAME:CONDITION"
    #   --stat-condition-remove "NAME:CONDITION"
    #   --stat-concentrate "NAME:SPELL"       (empty SPELL = clear)
    #   --stat-inventory-add    "NAME:ITEM"
    #   --stat-inventory-remove "NAME:ITEM"
    #
    # Timed effect flags:
    #   --effect-start "NAME:SPELL:DURATION"   DURATION: 10r/60m/8h/indef  optional :conc
    #   --effect-end   "NAME:SPELL"            narrative end (broken/dispelled)
"""

import sys
import json
import argparse
import os
import re
import select
import ssl
import stat
import time
import uuid
import urllib.request
from pathlib import Path

# Windows CJK fix: Python decodes stdin via the system codepage (GBK), but
# heredocs/pipes deliver UTF-8 bytes — force UTF-8 to keep Chinese narration intact.
for _stream in (sys.stdin, sys.stdout):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


_DISPLAY_DIR = os.path.dirname(os.path.abspath(__file__))
if _DISPLAY_DIR not in sys.path:
    sys.path.insert(0, _DISPLAY_DIR)
from runtime_paths import rt          # writable runtime dir (update-safe)
from scene_engine import MOODS, SETTINGS, SETTING_ALIASES, SceneContext, WorldNotes, derive, is_continuation
import scribe                         # background turn log; never blocks a send
_SCHEME_FILE = os.path.join(_DISPLAY_DIR, ".scheme")   # launch marker → code dir
_SCHEME = open(_SCHEME_FILE, encoding="utf-8").read().strip() if os.path.exists(_SCHEME_FILE) else "http"
BASE_URL    = f"{_SCHEME}://localhost:5001"
FLASK_URL   = f"{BASE_URL}/chunk"
STATS_URL   = f"{BASE_URL}/stats"
CLEAR_URL   = f"{BASE_URL}/clear"
HEALTH_URL  = f"{BASE_URL}/health"
DICE_REQ_URL = f"{BASE_URL}/dice-request"
SETUP_URL  = f"{BASE_URL}/setup-question"
TOKEN_FILE  = rt(".token")
TIMEOUT     = 8.0
RETRIES     = 1                # one retry on timeout/connection error
CHUNK_LIMIT = 3500             # paragraph-split text bodies above this many chars

# Send-side counters tracked across the script's lifetime (one process per call).
# Used by the post-send self-check to detect skipped or failed sends BEFORE the
# script exits, surfacing problems to stderr where Bash output is visible.
_SEND_LOG: list = []  # entries: {"endpoint": "chunk"/"stats", "ok": bool, "reason": str, "size": int}

# SSL context — only used when running HTTPS (self-signed cert)
if _SCHEME == "https":
    _SSL_CTX = ssl.create_default_context()
    _SSL_CTX.check_hostname = False
    _SSL_CTX.verify_mode = ssl.CERT_NONE
else:
    _SSL_CTX = None


def _read_token() -> str:
    try:
        return open(TOKEN_FILE, encoding="utf-8").read().strip()
    except FileNotFoundError:
        return ""


def _endpoint_label(url: str) -> str:
    """Short label for stderr reporting and the send-log."""
    if url.endswith("/chunk"):  return "chunk"
    if url.endswith("/stats"):  return "stats"
    if url.endswith("/health"): return "health"
    return url.rsplit("/", 1)[-1] or url


def _post(url: str, data: bytes, token: str) -> bool:
    """POST data with retries. Logs every send (success or failure) to _SEND_LOG.

    Returns True on success, False after all retries exhausted. Display being
    offline is the only "expected" failure mode; everything else is logged so
    transient timeouts / dropped sends do not silently lose narration.
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-DND-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    attempts = RETRIES + 1
    last_err: "Exception | None" = None
    label = _endpoint_label(url)
    for i in range(attempts):
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX)
            status = getattr(resp, "status", 200)
            # Server uses 204 (no content) for success; treat any 2xx as OK.
            if 200 <= status < 300:
                _SEND_LOG.append({"endpoint": label, "ok": True, "status": status, "size": len(data)})
                return True
            # Non-2xx: surface body to stderr for diagnosis.
            body_excerpt = resp.read(200).decode("utf-8", errors="replace") if hasattr(resp, "read") else ""
            print(f"send.py: POST {url} returned status {status}: {body_excerpt}", file=sys.stderr)
            _SEND_LOG.append({"endpoint": label, "ok": False, "reason": f"http {status}", "size": len(data)})
            return False
        except urllib.error.HTTPError as e:
            # 4xx/5xx — capture body so we can see the auth/validation reason.
            body_excerpt = ""
            try:
                body_excerpt = e.read(200).decode("utf-8", errors="replace")
            except Exception:
                pass
            print(f"send.py: POST {url} HTTP {e.code}: {body_excerpt}", file=sys.stderr)
            _SEND_LOG.append({"endpoint": label, "ok": False, "reason": f"http {e.code}", "size": len(data)})
            return False
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            inner = getattr(e, "reason", e)
            if isinstance(inner, ConnectionRefusedError) or "Connection refused" in str(inner):
                _SEND_LOG.append({"endpoint": label, "ok": False, "reason": "display offline", "size": len(data)})
                return False
            last_err = e
            if i < attempts - 1:
                time.sleep(0.5 * (i + 1))
        except Exception as e:
            last_err = e
            if i < attempts - 1:
                time.sleep(0.5 * (i + 1))
    print(f"send.py: POST {url} failed after {attempts} attempts: {last_err}",
          file=sys.stderr)
    _SEND_LOG.append({"endpoint": label, "ok": False, "reason": str(last_err), "size": len(data)})
    return False


def _validate_payload(payload: dict, endpoint: str) -> "list[str]":
    """Return a list of validation issues with the payload. Empty list = OK.

    Run BEFORE posting. Catches malformed sends that the server would accept
    but render incorrectly (e.g. text-content flag set but text body missing).
    """
    issues: list[str] = []
    if endpoint == "chunk":
        # Chunk payloads must carry recognizable content.
        text = payload.get("text", "")
        has_text = bool(text and str(text).strip())
        has_award = bool(payload.get("inspiration_award") or payload.get("xp_award")
                         or payload.get("milestone_award") or payload.get("milestone_spend"))
        if not has_text and not has_award:
            issues.append("chunk payload has no text and no award flag")
        # Mutually exclusive content tags
        content_tags = [k for k in ("player", "npc", "dice", "tutor", "action") if payload.get(k)]
        if len(content_tags) > 1:
            issues.append(f"chunk payload has multiple content tags: {content_tags}")
    elif endpoint == "stats":
        if "players" in payload and not isinstance(payload["players"], list):
            issues.append("stats payload 'players' is not a list")
    return issues


def _verify_health() -> "dict | None":
    """GET /health and return the server's status dict, or None if unreachable.

    Used by --verify to confirm the display saw the send. The /health endpoint
    returns counts for tail buffer, text log, current campaign, etc.
    """
    headers = {}
    token = _read_token()
    if token:
        headers["X-DND-Token"] = token
    req = urllib.request.Request(HEALTH_URL, headers=headers, method="GET")
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"send.py: /health unreachable ({e})", file=sys.stderr)
        return None


_CAMPAIGN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# Kokoro ids (af_heart, bm_lewis, blends with "+") or a Gemini name (Kore).
_VOICE_ID = re.compile(r"[a-z]{2}_[a-z0-9]+(\+[a-z]{2}_[a-z0-9]+)*|[A-Z][a-z]+")


def _derive_scene(cue: str, mood: "str | None"):
    """Derive the full scene cue from the display's current scene and the campaign's world notes."""
    health = _verify_health() or {}
    current = health.get("scene") if isinstance(health.get("scene"), dict) else None
    name, root = health.get("campaign"), health.get("data_root")
    world = WorldNotes()
    if isinstance(name, str) and _CAMPAIGN_NAME.fullmatch(name) and isinstance(root, str) and root:
        world = WorldNotes.load(Path(root) / "campaigns" / name)
    derived = derive(cue, mood, world=world, current=current)
    return derived, is_continuation(derived, current, mood)


def _ensure_active_campaign() -> "Path | None":
    """Check the display shows the portal's campaign; return that campaign's folder."""
    name = os.environ.get("DND_ACTIVE_CAMPAIGN", "").strip()
    if not name:
        return None
    if not _CAMPAIGN_NAME.fullmatch(name):
        raise SystemExit("send.py: invalid DND_ACTIVE_CAMPAIGN")
    scripts_dir = os.path.abspath(os.path.join(_DISPLAY_DIR, "..", "scripts"))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from paths import campaign_dir

    expected_tail = str(campaign_dir(name) / "session_tail.json")
    health = _verify_health()
    if health is None:
        raise SystemExit("send.py: display unavailable for active campaign check")
    if health.get("data_root") != str(campaign_dir(name).parent.parent):
        raise SystemExit("send.py: display campaign root differs from DND_CAMPAIGN_ROOT")
    if health.get("campaign") != name:
        if not _post(CLEAR_URL, b"{}", _read_token()):
            raise SystemExit("send.py: failed to clear previous campaign display")
        payload = json.dumps({"campaign": name}).encode("utf-8")
        if not _post(STATS_URL, payload, _read_token()):
            raise SystemExit("send.py: failed to select active campaign")
        health = _verify_health()
    if health is None or health.get("tail_path") != expected_tail:
        raise SystemExit("send.py: display campaign root differs from DND_CAMPAIGN_ROOT")
    return campaign_dir(name)


def _split_paragraphs(text: str, limit: int = CHUNK_LIMIT) -> list:
    """Split a long text body into chunks no larger than `limit` chars.

    Splits on paragraph boundaries (`\\n\\n`) when possible. Each chunk
    preserves the original whitespace. If a single paragraph exceeds the
    limit, it is hard-split on character boundary as a last resort.
    """
    if len(text) <= limit:
        return [text]
    paragraphs = text.split("\n\n")
    chunks: list = []
    cur = ""
    for p in paragraphs:
        candidate = (cur + "\n\n" + p) if cur else p
        if len(candidate) <= limit:
            cur = candidate
            continue
        # Flush whatever we have, start a new chunk with this paragraph
        if cur:
            chunks.append(cur)
            cur = ""
        # If this single paragraph is itself too big, hard-split it
        if len(p) > limit:
            for i in range(0, len(p), limit):
                chunks.append(p[i:i + limit])
        else:
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


_BARE_QUESTION = re.compile(
    r"(?:[A-Z][\w'-]*(?:\s+(?:and\s+)?[A-Z][\w'-]*){0,3},\s*)*"
    r"(?i:(?:so|and|now|well|then),?\s+)?"
    r"(?i:what (?:would|will|do|should) you(?: all| two)? (?:(?:like|want|wish|care) to )?do"
    r"|where (?:would|do|will) you(?: all| two)? (?:(?:like|want|wish|care) to )?go"
    r"|where to|where next|what next|what now|what's your next move)"
    r"(?i:,? (?:now|next|first|then))?\s*[?.!]*"
)


def bare_question_ending(text: str) -> "str | None":
    """Return the final sentence when it is only a 'what do you do' prompt."""
    body = text.strip().strip("\"'“”‘’ ")
    if not body:
        return None
    last = re.split(r"(?<=[.!?])\s+|(?<=[.!?][\"'”’])\s+", body)[-1].strip("\"'“”‘’ ")
    return last if _BARE_QUESTION.fullmatch(last) else None


_BLOCK_KINDS = {"player": "player", "npc": "npc", "dice": "dice", "tutor": "tutor", "action": "action",
                "narration": None, "dm": None}
_NAMED_BLOCKS = {"player", "npc", "action"}
_MARKER = re.compile(r"^@(player|npc|dice|tutor|action|narration|dm)(?:[ \t]+(.*?))?[ \t]*$")
_MARKER_VOICE = re.compile(r"^(.*?)[ \t]+voice=(.*)$")


def parse_blocks(text: str) -> "list[dict]":
    """Split one body into display blocks at marker lines; [] when there is no text.

    Each block is {"kind": None|"player"|"npc"|"dice"|"tutor"|"action", "name", "voice", "text"};
    kind None is narration. Raises ValueError on a marker without its name or with a bad voice.
    """
    blocks: "list[dict]" = []
    current = {"kind": None, "name": None, "voice": None, "lines": []}
    for line in text.splitlines():
        marker = _MARKER.match(line.strip()) if line.lstrip().startswith("@") else None
        if not marker:
            current["lines"].append(line)
            continue
        kind, rest = _BLOCK_KINDS[marker.group(1)], (marker.group(2) or "").strip()
        if rest and marker.group(1) not in _NAMED_BLOCKS:  # "@dice Nella rolls 17" is text, not a marker
            current["lines"].append(line)
            continue
        blocks.append(current)
        voice = None
        if kind == "npc":
            with_voice = _MARKER_VOICE.match(rest)
            if with_voice:
                rest, voice = with_voice.group(1).strip(), with_voice.group(2).strip()
                if not _VOICE_ID.fullmatch(voice):
                    raise ValueError(f"@npc {rest}: voice takes an id such as bf_alice, not {voice!r}")
        if kind in _NAMED_BLOCKS and not rest:
            raise ValueError(f"@{marker.group(1)} needs a name, for example @{marker.group(1)} Nella")
        current = {"kind": kind, "name": rest or None, "voice": voice, "lines": []}
    blocks.append(current)
    out = []
    for block in blocks:
        body = "\n".join(block.pop("lines")).strip()
        if body:
            out.append(dict(block, text=body))
    return out


def _flag_block(args, text: str) -> "list[dict]":
    """The single block a content flag (or none) makes of the whole body."""
    if not text.strip():
        return []
    for kind in ("action", "player", "npc"):
        if getattr(args, kind):
            return [{"kind": kind, "name": getattr(args, kind), "text": text,
                     "voice": args.npc_voice.strip() if kind == "npc" and args.npc_voice else None}]
    for kind in ("dice", "tutor"):
        if getattr(args, kind):
            return [{"kind": kind, "name": None, "voice": None, "text": text}]
    return [{"kind": None, "name": None, "voice": None, "text": text}]


def _waiting_stdin() -> str:
    """A heredoc or piped body for --dice-request; "" for a terminal, /dev/null or a pipe nobody writes.

    A heredoc arrives as a regular file or a pipe that is ready at once. A pipe that stays empty for a
    second is a caller's open stdin, not a body, and is never read, so a standalone request can't hang.
    """
    try:
        if sys.stdin.isatty():
            return ""
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (ValueError, OSError, AttributeError):  # no file descriptor (tests, embedded callers)
        mode = None
    if mode is not None and stat.S_ISCHR(mode):
        return ""
    if mode is not None and not stat.S_ISREG(mode) and os.name != "nt":
        if not select.select([sys.stdin], [], [], 1.0)[0]:
            return ""
    try:
        return sys.stdin.read()
    except OSError:
        return ""


def _build_stats_payload(args) -> "dict | None":
    """Build a push_stats-compatible payload from --stat-* flags."""
    players: "dict[str, dict]" = {}

    def _p(name: str) -> dict:
        return players.setdefault(name, {"name": name})

    for spec in (args.stat_hp or []):
        parts = spec.split(":")
        if len(parts) >= 3:
            name, cur, mx = parts[0], parts[1], parts[2]
            try:
                _p(name)["hp"] = {"current": int(cur), "max": int(mx)}
            except ValueError:
                pass

    for spec in (args.stat_temp_hp or []):
        idx = spec.rfind(":")
        if idx > 0:
            name, n = spec[:idx], spec[idx + 1:]
            try:
                _p(name).setdefault("hp", {})["temp"] = int(n)
            except ValueError:
                pass

    for spec in (args.stat_slot_use or []):
        idx = spec.rfind(":")
        if idx > 0:
            name, lvl = spec[:idx], spec[idx + 1:]
            try:
                _p(name)["_slot_use"] = int(lvl)
            except ValueError:
                pass

    for spec in (args.stat_slot_restore or []):
        idx = spec.rfind(":")
        if idx > 0:
            name, lvl = spec[:idx], spec[idx + 1:]
            try:
                _p(name)["_slot_restore"] = int(lvl)
            except ValueError:
                pass

    for spec in (args.stat_condition_add or []):
        idx = spec.find(":")
        if idx > 0:
            name, cond = spec[:idx], spec[idx + 1:]
            if cond.strip():
                _p(name)["_conditions_add"] = cond.strip()

    for spec in (args.stat_condition_remove or []):
        idx = spec.find(":")
        if idx > 0:
            name, cond = spec[:idx], spec[idx + 1:]
            if cond.strip():
                _p(name)["_conditions_remove"] = cond.strip()

    for spec in (args.stat_concentrate or []):
        idx = spec.find(":")
        if idx >= 0:
            name, spell = spec[:idx], spec[idx + 1:]
            _p(name)["concentration"] = spell.strip() or None

    for spec in (args.stat_inventory_add or []):
        idx = spec.find(":")
        if idx > 0:
            name, item = spec[:idx], spec[idx + 1:]
            if item.strip():
                _p(name)["_inventory_add"] = item.strip()

    for spec in (args.stat_inventory_remove or []):
        idx = spec.find(":")
        if idx > 0:
            name, item = spec[:idx], spec[idx + 1:]
            if item.strip():
                _p(name)["_inventory_remove"] = item.strip()

    for spec in (args.effect_start or []):
        # Format: NAME:SPELL:DURATION[:conc]
        # DURATION: 10r (rounds), 60m (minutes), 8h (hours), indef (indefinite)
        parts = spec.split(":", 3)
        if len(parts) < 3:
            continue
        name     = parts[0].strip()
        spell    = parts[1].strip()
        dur_str  = parts[2].strip().lower()
        is_conc  = len(parts) == 4 and parts[3].strip().lower() == "conc"
        if not name or not spell:
            continue
        effect: dict = {"name": spell, "concentration": is_conc}
        if dur_str.endswith("r"):
            try:
                effect["duration_type"]      = "rounds"
                effect["duration_remaining"] = int(dur_str[:-1])
            except ValueError:
                continue
        elif dur_str.endswith("m"):
            try:
                effect["duration_type"]    = "minutes"
                effect["duration_seconds"] = int(dur_str[:-1]) * 60
                effect["started_at"]       = time.time()
            except ValueError:
                continue
        elif dur_str.endswith("h"):
            try:
                effect["duration_type"]    = "hours"
                effect["duration_seconds"] = int(dur_str[:-1]) * 3600
                effect["started_at"]       = time.time()
            except ValueError:
                continue
        else:
            effect["duration_type"] = "indefinite"
        _p(name)["_effect_start"] = effect

    for spec in (args.effect_end or []):
        idx = spec.find(":")
        if idx > 0:
            name, spell = spec[:idx], spec[idx + 1:]
            if spell.strip():
                _p(name)["_effect_end"] = spell.strip()

    if not players:
        return None
    return {"players": list(players.values())}


def _dice_request(args) -> None:
    """Ask the phones for a roll; with --wait, block until it lands and print each roll line."""
    # Comma-split character list so the DM can address multiple players at once,
    # e.g. --character "Piper,Mira,Aldric"  → all three must roll before --wait returns.
    chars = [c.strip() for c in (args.character or "any").split(",") if c.strip()] or ["any"]
    body = {
        "characters": chars,
        "spec": args.spec,
        "modifier": args.modifier,
        "advantage": args.advantage,
    }
    if args.label: body["label"] = args.label
    if args.dc is not None: body["dc"] = args.dc
    _token = _read_token()

    # Direct urllib call (rather than _post) because we need the JSON response body.
    headers = {"Content-Type": "application/json"}
    if _token: headers["X-DND-Token"] = _token
    try:
        req = urllib.request.Request(
            DICE_REQ_URL, data=json.dumps(body).encode(),
            headers=headers, method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX)
        resp_body = json.loads(resp.read().decode("utf-8") or "{}")
    except Exception as e:
        print(f"send.py: dice-request failed: {e}", file=sys.stderr)
        sys.exit(1)

    request_id = resp_body.get("request_id", "")
    pending    = list(resp_body.get("pending") or [])
    # Machine-readable on stdout so callers can correlate (cancel, log, etc.).
    # flush=True because Python block-buffers stdout when redirected, and
    # callers want the id immediately to dispatch rolls / cancellation.
    print(request_id, flush=True)

    if args.wait:
        if not pending:
            # "any" targets aren't tracked, so --wait is a no-op there.
            print(f"send.py: --wait with no trackable characters (target={chars}) — nothing to wait for.",
                  file=sys.stderr)
        else:
            status_url = f"{DICE_REQ_URL}/{request_id}"
            poll_headers = {"X-DND-Token": _token} if _token else {}
            deadline = time.time() + max(1, args.wait_timeout)
            last_pending: list = list(pending)
            print(f"send.py: waiting for rolls from: {', '.join(pending)}. This command returns by itself "
                  "the moment they roll. If your shell hands it back early, wait on it once with a long "
                  "timeout (yield_time_ms 120000); polling every few seconds only delays the result.",
                  file=sys.stderr, flush=True)
            while time.time() < deadline:
                try:
                    sreq = urllib.request.Request(status_url, headers=poll_headers, method="GET")
                    s = urllib.request.urlopen(sreq, timeout=TIMEOUT, context=_SSL_CTX)
                    st = json.loads(s.read().decode("utf-8") or "{}")
                except Exception as e:
                    print(f"send.py: poll error ({e}) — retrying", file=sys.stderr)
                    time.sleep(1.0)
                    continue
                if st.get("complete"):
                    print(f"send.py: all rolls received.", file=sys.stderr)
                    for line in st.get("results") or []:
                        print(line, flush=True)
                    break
                remaining = st.get("pending") or []
                if remaining != last_pending:
                    print(f"send.py: still waiting on: {', '.join(remaining)}", file=sys.stderr)
                    last_pending = list(remaining)
                time.sleep(0.5)
            else:
                print(f"send.py: timeout after {args.wait_timeout}s — still pending: "
                      f"{', '.join(last_pending)}", file=sys.stderr)
                sys.exit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send text to the DnD display server.")
    parser.add_argument("--setup-question", nargs="?", const="", metavar="TEXT",
                        help="Show a setup question beside Party Input without adding story history; pass text or pipe stdin")
    parser.add_argument(
        "--player", metavar="NAME",
        help="Send as a player action, prepending the character name on display",
    )
    parser.add_argument(
        "--npc", metavar="NAME",
        help="Send as NPC dialogue with amber styling and character name header",
    )
    parser.add_argument(
        "--npc-voice", metavar="VOICE",
        help="With --npc: save this TTS voice for the NPC (e.g. bf_alice). "
             "A voice picked by hand in the voice menu is kept",
    )
    parser.add_argument(
        "--dice", action="store_true",
        help="Send as a dice result (inline gold styling)",
    )
    parser.add_argument(
        "--tutor", action="store_true",
        help="Send as a tutor/learning hint (collapsible parchment block)",
    )
    parser.add_argument(
        "--opener", action="store_true",
        help="DM narration only: the first sentence or two, sent the moment the "
             "outcome is decided so the table hears it while the rest is written. "
             "The display holds the paragraph open for the next narration send.",
    )
    parser.add_argument("--scene", metavar="'PLACE: WHAT IS HAPPENING'",
        help="Scene change in one line, bundled with DM narration. The place (before the colon) "
             "sets the key, setting, saved place theme and era; repeating the current place changes nothing")
    parser.add_argument("--mood", choices=sorted(MOODS),
        help="Optional mood for --scene; otherwise the saved place mood or the setting's usual tone")
    parser.add_argument("--music-event", choices=["scene", "combat-start", "combat-end"],
        help="Scene-entry, combat-start or combat-end music cue bundled with DM narration")
    parser.add_argument("--music-setting", metavar="SETTING", choices=sorted(SETTINGS.union(SETTING_ALIASES)),
        help="Setting for --music-event scene, such as cave, forest or tavern; tower uses arcane")
    parser.add_argument("--music-mood", choices=["calm", "curious", "ominous", "tense", "warm"],
        default=None, help="Optional mood for a scene cue")
    parser.add_argument("--scene-key", help="Stable scene identity, reused while this place or activity continues")
    parser.add_argument("--scene-label", help="Short player-facing name for this scene")
    parser.add_argument("--scene-description", help="Specific scenery, activity and atmosphere for image and music generation")
    parser.add_argument("--scene-era", help="Established world era for image clothing, architecture and technology; omitted era inherits")
    parser.add_argument("--scene-theme", help="JSON ancestry array of place names and optional moods; [] clears inheritance")
    parser.add_argument(
        "--action", metavar="NAME",
        help="Send as a player action intent — subdued label echoing what the player declared",
    )

    # ── Inspiration / XP award flags ─────────────────────────────────────────
    parser.add_argument("--inspiration-award", metavar="NAME",
        help="Award Inspiration: fires a styled gold block in the feed + sidebar badge")
    parser.add_argument("--inspiration-reason", metavar="TEXT",
        help="Optional reason to render below the name in the inspiration block "
             "(matches how --xp-award reason is rendered). Requires --inspiration-award.")
    parser.add_argument("--inspiration-spend", metavar="NAME",
        help="Spend/clear Inspiration: removes sidebar badge")
    parser.add_argument("--milestone-award", metavar="NAME",
        help="Award a stack-based reward token (Bardic Inspiration die, homebrew "
             "Hero Coin, etc.). Use with --milestone-label / --milestone-reason.")
    parser.add_argument("--milestone-spend", metavar="NAME",
        help="Spend a stack-based reward token; decrements the sidebar counter")
    parser.add_argument("--milestone-reason", metavar="TEXT",
        help="Optional reason rendered inside the milestone-award block")
    parser.add_argument("--milestone-label", metavar="TEXT",
        help='Label for the reward type (default: "Milestone"). Examples: '
             '"Bardic Inspiration", "Hero Coin", "Fate Token".')
    parser.add_argument("--xp-award", metavar="JSON",
        help='XP award block: \'{"names":["Aldric","Mira"],"xp":250,"reason":"Encounter resolved","total":"3250/6500"}\'')

    # ── Dice request (DM → player phones) ────────────────────────────────────
    parser.add_argument("--dice-request", action="store_true",
        help='Broadcast a dice request to player phones (pre-fills their pad). '
             'Combine with --character / --spec / --modifier / --advantage / --label / --dc.')
    parser.add_argument("--character", metavar="NAME",
        help='Target character for --dice-request. Use "any" or omit to target every phone.')
    parser.add_argument("--spec", metavar="NdM", default="1d20",
        help='Dice spec for --dice-request (default 1d20). Examples: 1d20, 2d6, 1d100.')
    parser.add_argument("--modifier", type=int, default=0, metavar="N",
        help='Modifier to apply on the phone (default 0). May be negative.')
    parser.add_argument("--advantage", choices=["normal", "advantage", "disadvantage"],
        default="normal",
        help='For 1d20 only: normal | advantage | disadvantage (default normal).')
    parser.add_argument("--label", metavar="TEXT",
        help='Human label for the roll (e.g. "Stealth check", "Concentration save").')
    parser.add_argument("--dc", type=int, metavar="N",
        help='Optional DC; displayed informationally on the phone.')
    parser.add_argument("--wait", action="store_true",
        help='With --dice-request: block until every prescribed character has rolled '
             '(polls /dice-request/<id>), then print each roll line on stdout. '
             'Exits non-zero on timeout.')
    parser.add_argument("--wait-timeout", type=int, default=120, metavar="SECONDS",
        help='Timeout for --wait (default 120s). On timeout, prints still-pending '
             'characters to stderr and exits 2.')

    # ── Stat-change flags (Option B — bundled with narration) ─────────────────
    parser.add_argument("--stat-hp", action="append", metavar="NAME:CUR:MAX",
        help="Set HP: NAME:CURRENT:MAX (can repeat for multiple players)")
    parser.add_argument("--stat-temp-hp", action="append", metavar="NAME:N",
        help="Set temp HP: NAME:N")
    parser.add_argument("--stat-slot-use", action="append", metavar="NAME:LEVEL",
        help="Expend one spell slot: NAME:LEVEL")
    parser.add_argument("--stat-slot-restore", action="append", metavar="NAME:LEVEL",
        help="Restore one spell slot: NAME:LEVEL")
    parser.add_argument("--stat-condition-add", action="append", metavar="NAME:COND",
        help="Add condition: NAME:CONDITION (can repeat)")
    parser.add_argument("--stat-condition-remove", action="append", metavar="NAME:COND",
        help="Remove condition: NAME:CONDITION (can repeat)")
    parser.add_argument("--stat-concentrate", action="append", metavar="NAME:SPELL",
        help="Set concentration: NAME:SPELL (empty SPELL = clear)")
    parser.add_argument("--stat-inventory-add", action="append", metavar="NAME:ITEM",
        help="Add inventory item: NAME:ITEM")
    parser.add_argument("--stat-inventory-remove", action="append", metavar="NAME:ITEM",
        help="Remove inventory item: NAME:ITEM")
    parser.add_argument("--effect-start", action="append", metavar="NAME:SPELL:DURATION",
        help="Start a timed effect: NAME:SPELL:DURATION (10r/60m/8h/indef) optionally :conc")
    parser.add_argument("--effect-end", action="append", metavar="NAME:SPELL",
        help="End a timed effect: NAME:SPELL (narrative end — broken, dispelled, player drops)")

    # ── Diagnostics ───────────────────────────────────────────────────────────
    parser.add_argument("--verify", action="store_true",
        help="After sending, GET /health and confirm the broadcast was received. "
             "Surfaces a clear stderr line on mismatch — use during dev/debug.")

    args = parser.parse_args()
    if args.setup_question is not None:
        question = args.setup_question.strip() if args.setup_question else sys.stdin.read().strip()
        if not question or len(question) > 1000:
            parser.error("--setup-question requires 1-1000 characters on stdin")
        _ensure_active_campaign()
        token = _read_token()
        if not _post(SETUP_URL, json.dumps({"text": question}).encode("utf-8"), token):
            if not _post(FLASK_URL, json.dumps({"text": question}).encode("utf-8"), token):
                raise SystemExit("send.py: setup question did not reach display")
        return
    long_scene_flags = (args.music_event, args.music_setting, args.music_mood, args.scene_key,
                        args.scene_label, args.scene_description, args.scene_era, args.scene_theme)
    if args.scene is not None:
        if any(value is not None for value in long_scene_flags):
            parser.error("--scene replaces --music-* and --scene-* flags; use one form")
        try:
            derive(args.scene, args.mood)
        except ValueError as error:
            parser.error(str(error))
    elif args.mood is not None:
        parser.error("--mood requires --scene")
    if args.music_event:
        if args.music_event == "scene" and not args.music_setting:
            parser.error("--music-event scene requires --music-setting")
        if args.music_event != "scene" and args.music_setting:
            parser.error("--music-setting only applies to --music-event scene")
    elif args.music_setting or args.music_mood:
        parser.error("--music-setting/--music-mood require --music-event")
    scene = None
    if args.scene_theme is not None and not all((args.scene_key, args.scene_label, args.scene_description)):
        parser.error("--scene-theme requires --scene-key, --scene-label and --scene-description")
    if args.scene_era is not None and not all((args.scene_key, args.scene_label, args.scene_description)):
        parser.error("--scene-era requires --scene-key, --scene-label and --scene-description")
    if any(value is not None for value in (args.scene_key, args.scene_label, args.scene_description)):
        if args.music_event != "scene":
            parser.error("--scene-key/--scene-label/--scene-description require --music-event scene")
        try:
            value = {"key": args.scene_key, "label": args.scene_label, "description": args.scene_description}
            if args.scene_theme is not None:
                value["theme"] = json.loads(args.scene_theme)
            if args.scene_era is not None:
                value["era"] = args.scene_era
            scene = SceneContext.parse(value)
        except ValueError as error:
            parser.error(str(error))
    if args.npc_voice is not None and not args.npc:
        parser.error("--npc-voice requires --npc")
    if args.npc_voice is not None and not _VOICE_ID.fullmatch(args.npc_voice.strip()):
        parser.error("--npc-voice takes a voice id such as bf_alice or am_onyx")
    # Two categories of flags drive whether to read stdin:
    #   1. Content flags (--player/--npc/--dice/--tutor/--action): body REQUIRED.
    #      Always read stdin — script will abort below if the body is empty.
    #   2. Everything else (plain narration, stat-only, award-only, award+body):
    #      body OPTIONAL. Read stdin when piped (heredoc/pipe), skip when an
    #      interactive TTY to avoid blocking on an unattended call.
    #
    # Award flags (--inspiration-*/--xp-*/--milestone-*) fall into category 2:
    # reading piped stdin under the isatty() gate lets bundled narration flow
    # through to the text-send block while bare award calls don't block.
    _has_content_flag = bool(args.player or args.npc or args.dice or args.tutor or args.action)
    # Plain narration piped with --dice-request goes out first, so the table hears the DC and the
    # stake before the phones ask for the roll.
    narration_first = bool(args.dice_request) and not _has_content_flag
    if _has_content_flag:
        text = sys.stdin.read()
    elif narration_first:
        text = _waiting_stdin()
    else:
        text = "" if sys.stdin.isatty() else sys.stdin.read()
    if _has_content_flag or args.opener:
        blocks = _flag_block(args, text)
    else:
        try:
            blocks = parse_blocks(text)
        except ValueError as error:
            print(f"send.py: {error}", file=sys.stderr)
            sys.exit(2)
    music_event, music_setting, music_mood = args.music_event, args.music_setting, args.music_mood
    campaign_folder = _ensure_active_campaign()
    if args.dice_request and not narration_first:
        _dice_request(args)
        _other = (args.player or args.npc or args.dice or args.tutor or args.action
                  or args.inspiration_award or args.inspiration_spend
                  or args.milestone_award or args.milestone_spend or args.xp_award
                  or args.verify)
        if not _other:
            return
    scene_note = None
    if args.scene is not None:
        derived, unchanged = _derive_scene(args.scene, args.mood)
        scene_note = (f"scene unchanged: {derived.scene.label} ({derived.scene.key})" if unchanged else
                      f"scene: {derived.scene.label} ({derived.scene.key}; {derived.setting}, {derived.mood}"
                      + (f", {derived.scene.era})" if derived.scene.era else ")"))
        if not unchanged:
            music_event, music_setting, music_mood, scene = "scene", derived.setting, derived.mood, derived.scene
    music_id = uuid.uuid4().hex if music_event else None

    token = _read_token()

    # ── Inspiration award/spend ──────────────────────────────────────────────
    # Award/spend flags POST their styled block + stat update first, then fall
    # through to the text-send block below. Bundling a heredoc body with an
    # award flag now broadcasts BOTH the gold block AND the narration chunk —
    # previously the narration was silently dropped by an early return here.
    if args.inspiration_award:
        name = args.inspiration_award.strip()
        body: dict = {"inspiration_award": name, "text": name}
        if args.inspiration_reason:
            body["reason"] = args.inspiration_reason.strip()
        _post(FLASK_URL, json.dumps(body).encode(), token)
        _post(STATS_URL, json.dumps({"players": [{"name": name, "inspiration": True}]}).encode(), token)

    if args.inspiration_spend:
        name = args.inspiration_spend.strip()
        _post(STATS_URL, json.dumps({"players": [{"name": name, "inspiration": False}]}).encode(), token)

    # ── Milestone award/spend (stack-based reward — system-agnostic) ─────────
    # Distinct from --inspiration-award: that one is the binary D&D 5e badge,
    # this one is a count that accumulates. Use for Bardic Inspiration dice,
    # homebrew Hero Coins, Fate Tokens, or alternate reward systems.
    if args.milestone_award:
        name = args.milestone_award.strip()
        label = (args.milestone_label or "Milestone").strip()
        m_body: dict = {"milestone_award": name, "text": name, "label": label}
        if args.milestone_reason:
            m_body["reason"] = args.milestone_reason.strip()
        _post(FLASK_URL, json.dumps(m_body).encode(), token)
        _post(STATS_URL, json.dumps({
            "players": [{"name": name, "_milestone_inc": label}]
        }).encode(), token)

    if args.milestone_spend:
        name = args.milestone_spend.strip()
        label = (args.milestone_label or "Milestone").strip()
        ms_body: dict = {"milestone_spend": name, "text": name, "label": label}
        _post(FLASK_URL, json.dumps(ms_body).encode(), token)
        _post(STATS_URL, json.dumps({
            "players": [{"name": name, "_milestone_dec": label}]
        }).encode(), token)

    # ── XP award block ────────────────────────────────────────────────────────
    if args.xp_award:
        try:
            xp_data = json.loads(args.xp_award)
        except json.JSONDecodeError as e:
            print(f"Invalid xp-award JSON: {e}", file=sys.stderr)
            sys.exit(1)
        # Build a human-readable summary if not provided
        if "summary" not in xp_data:
            names = ", ".join(xp_data.get("names", []))
            amt   = xp_data.get("xp", 0)
            rsn   = xp_data.get("reason", "")
            xp_data["summary"] = f"{names} — {amt} XP" + (f" ({rsn})" if rsn else "")
        _post(FLASK_URL, json.dumps({"xp_award": xp_data, "text": xp_data["summary"]}).encode(), token)

    # ── Pre-flight integrity check ────────────────────────────────────────────
    # If a content flag is set but no text arrived on stdin, that's almost
    # certainly the heredoc-routing bug surfacing again. Bail loudly so the
    # caller knows the send was rejected rather than silently producing an
    # empty broadcast.
    if _has_content_flag and not text.strip():
        flag = (
            "--player" if args.player else
            "--npc"    if args.npc    else
            "--dice"   if args.dice   else
            "--tutor"  if args.tutor  else
            "--action"
        )
        print(f"send.py: ABORT — {flag} requires a text body but stdin was empty.",
              file=sys.stderr)
        sys.exit(2)

    if not (_has_content_flag or args.opener):
        narration = [b["text"] for b in blocks if b["kind"] is None]
        ending = bare_question_ending(narration[-1]) if narration else None
        if ending:
            print(f"send.py: warning: the narration ends on a bare question ({ending!r}). "
                  "End the turn on something in motion: a person who wants something now, "
                  "a deadline, a cost or a discovery. Sent anyway.", file=sys.stderr)

    # ── Text send ─────────────────────────────────────────────────────────────
    chunks_sent = 0
    # The scene cue rides on narration, never on the player's echoed action or NPC speech; a call
    # with no narration block carries it on its first block.
    music_kinds = {None} if any(b["kind"] is None for b in blocks) else {b["kind"] for b in blocks}
    music_pending = bool(music_id)
    delivered: "list[dict]" = []
    for block in blocks:
        kind = block["kind"]
        block_ok = True
        for chunk in _split_paragraphs(block["text"]):
            payload: dict = {"text": chunk}
            with_music = music_pending and kind in music_kinds
            if with_music:
                payload["music"] = {"event": music_event.replace("-", "_"),
                                    "id": music_id, "setting": music_setting}
                if music_mood is not None:
                    payload["music"]["mood"] = music_mood
                if scene:
                    payload["music"]["scene"] = scene.to_dict()
            if kind in ("action", "player", "npc"):
                payload[kind] = block["name"]
                if kind == "npc" and block["voice"]:
                    payload["npc_voice"] = block["voice"]
            elif kind in ("dice", "tutor"):
                payload[kind] = True
            elif args.opener:
                payload["opener"] = True

            issues = _validate_payload(payload, "chunk")
            if issues:
                print(f"send.py: chunk payload validation failed: {'; '.join(issues)}",
                      file=sys.stderr)
                sys.exit(2)
            ok = _post(FLASK_URL, json.dumps(payload).encode("utf-8"), token)
            block_ok = block_ok and ok
            if ok:
                chunks_sent += 1
                music_pending = music_pending and not with_music
        if block_ok:
            delivered.append(block)

    if music_event and chunks_sent == 0:
        print("send.py: music cue requires successfully sent narration", file=sys.stderr)
        sys.exit(2)
    if delivered and campaign_folder is not None:
        scribe.record(campaign_folder, [[" ".join(filter(None, (b["kind"] or "narration", b["name"]))), b["text"]]
                                        for b in delivered])
    if scene_note:
        print(scene_note, file=sys.stderr if args.dice_request else sys.stdout)

    # ── Stat send (bundled) ───────────────────────────────────────────────────
    stats_payload = _build_stats_payload(args)
    stats_sent = False
    if stats_payload:
        issues = _validate_payload(stats_payload, "stats")
        if issues:
            print(f"send.py: stats payload validation failed: {'; '.join(issues)}",
                  file=sys.stderr)
            sys.exit(2)
        stats_sent = _post(STATS_URL, json.dumps(stats_payload).encode("utf-8"), token)

    # ── Post-flight self-check ────────────────────────────────────────────────
    # Surface a clear, single-line summary if anything went sideways. Silent on
    # full success so successful sends don't add noise to Bash output.
    failed = [e for e in _SEND_LOG if not e["ok"]]
    if failed:
        offline = any(e.get("reason") == "display offline" for e in failed)
        if offline:
            # Display offline is a known/expected failure mode — one quiet line.
            print("send.py: display offline — send dropped silently",
                  file=sys.stderr)
        else:
            summary = ", ".join(f"{e['endpoint']}={e.get('reason','?')}" for e in failed)
            print(f"send.py: PARTIAL FAILURE — {summary}", file=sys.stderr)
            sys.exit(3)

    # ── Optional verify round-trip ────────────────────────────────────────────
    # When --verify is on, hit /health to confirm the chunks/stats counts
    # advanced. Detects "server accepted but didn't broadcast" regressions
    # without polluting normal operation.
    if args.verify:
        health = _verify_health()
        if health is None:
            print("send.py: --verify failed — /health unreachable", file=sys.stderr)
            sys.exit(4)
        # We just confirm the server is alive and tracking. Detailed verify
        # would compare counts pre/post, but health endpoint already proves
        # the server is processing requests in this address space.
        if chunks_sent and not health.get("alive"):
            print(f"send.py: --verify mismatch — server reports not alive: {health}",
                  file=sys.stderr)
            sys.exit(4)


    if narration_first:
        _dice_request(args)

if __name__ == "__main__":
    main()
