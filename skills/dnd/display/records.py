"""records.py — the turn log and NPC records, kept by send.py so the DM doesn't have to.

send.py calls `record_send()` once its blocks have reached the display. No model is involved
here; it runs in a few milliseconds inside the DM's narration call.

turn-log.md (in the campaign folder; this module is its only writer)
    # Turn log
    ## Session 7 · 2026-11-14              a new section after a gap of SESSION_GAP_S or more
    ### 21:04 · Brackenford toll gate · 18 Duskfall 1247   one entry per send: time, place, in-world date
    **Everybody:** We hand over the satchel.               one paragraph per block, word for word
    **DM:** The clerk breaks the seal.
    **Hessa Varn:** "Twenty silver, or the wagon stays."
  Speakers: DM (narration), DM hint (tutor), Dice, XP, Inspiration, Milestone, else the
  player, action or NPC name. The session number is one more than the newest `## Session N`
  in session-log.md, its archive or state.md. A replay (every block long and among the last
  RECENT_KEEP logged) is skipped.

npcs.md (shared with the DM and oracle.py, through npc_entries.edit and its lock)
  For each `@npc NAME` speaker: `- **Met:** <place>, session N (<in-world date>)` and
  `- **Voice:** <id>`, each added only when the entry has no such line. A speaker with no entry
  gets one only for a full name ("Tova Lind", not "Warden rider", "Bridge Keeper" or "Auditor"). Met is skipped
  for someone met before: their entry has a note line, an earlier session, or the DM's own
  description while the logs already name them (full or first name). `- **As seen:**`
  carries phrases the helper model copied from the narration (npc_describe.py). This module
  only ever adds lines; it never changes or removes one, so the DM's own notes always stand.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt  # noqa: E402  (also puts scripts/ on sys.path)

import npc_entries  # noqa: E402
import npc_describe  # noqa: E402
from paths import campaign_dir  # noqa: E402

LOG_NAME = "turn-log.md"
LOG_HEADER = ("# Turn log\n\nWritten by send.py: what the table was shown, word for word, one entry per send. "
              "Not edited by hand.\n")
SESSION_GAP_S = 3 * 3600
RECENT_KEEP = 40
REPLAY_MIN_CHARS = 40  # shorter lines ("Yes.") can honestly be said twice
LOOK_LABELS = ("As seen", "Look", "Looks", "Appearance", "Description")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SESSION_HEADING = re.compile(r"^#{2,3}\s+Session\s+(\d+)\b", re.MULTILINE)
_LOG_SECTION = re.compile(r"^## Session (\d+) · ", re.MULTILINE)
_SESSION_COUNT = re.compile(r"\*\*Session count:\*\*\s*(\d+)")
_FIELD = re.compile(r"^[-*]\s+\*\*[^*]+:\*\*")
_ANNOTATION = re.compile(r" \(| —| –| - |,|:")
_SESSION_MENTION = re.compile(r"\bsession\s+(\d+)", re.IGNORECASE)
# A speaker label ending in one of these is a role ("Bridge Keeper", "Warden Rider"), not a name.
_ROLE_WORDS = {"rider", "auditor", "keeper", "clerk", "guard", "guards", "warden", "traveller", "traveler", "carter",
               "drover", "bandit", "man", "woman", "stranger", "merchant", "courier", "driver", "soldier", "farmer",
               "innkeeper", "smith", "trader", "pedlar", "peddler", "tinker", "boy", "girl", "child", "watchman",
               "bowman", "messenger", "sailor", "priest", "monk", "official", "officer", "voice", "crowd", "patron",
               "captain", "sergeant", "lieutenant", "guardsman", "sentry", "porter", "servant", "steward"}
_TITLES = {"brother", "sister", "father", "mother", "captain", "sergeant", "lady", "lord", "sir", "dame", "master",
           "mistress", "old", "young", "mayor", "elder", "reeve", "warden", "auditor", "clerk", "abbot", "abbess"}
_NAME_PARTICLES = {"of", "the", "de", "del", "della", "di", "da", "du", "van", "von", "der", "den", "le", "la", "al",
                   "bin", "ibn", "ap"}
_LOCATION = re.compile(r"^[ \t]*[-*][ \t]*\*\*Location:\*\*[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)


def campaign_folder() -> "Path | None":
    """The campaign this send belongs to: DND_ACTIVE_CAMPAIGN, else the one the display shows."""
    name = os.environ.get("DND_ACTIVE_CAMPAIGN", "").strip()
    if not name:
        try:
            name = Path(rt(".campaign")).read_text(encoding="utf-8").strip()
        except OSError:
            return None
    if not _NAME.fullmatch(name):
        return None
    folder = campaign_dir(name)
    return folder if (folder / "state.md").is_file() else None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _state_path() -> str:
    return rt("records-state.json")


def _load_state() -> dict:
    try:
        data = json.loads(Path(_state_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict) -> None:
    path = Path(_state_path())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def next_session(log_text: str, archive_text: str, state_text: str) -> int:
    """One more than the newest session written up in session-log.md, its archive or state.md's
    Continuity Archive. session_summary.py numbers a new session-log.md entry the same way."""
    numbers = [int(n) for text in (log_text, archive_text, state_text) for n in _SESSION_HEADING.findall(text)]
    if numbers:
        return max(numbers) + 1
    count = _SESSION_COUNT.search(state_text)
    return int(count.group(1)) + 1 if count else 1


def session_number(folder: Path) -> int:
    return next_session(*(_read(folder / name) for name in ("session-log.md", "session-log-archive.md", "state.md")))


class Turns(NamedTuple):
    number: int
    text: str  # every section of that session, oldest first
    mtime_ns: int


def latest_session(folder: Path) -> "Turns | None":
    """The newest session in turn-log.md. A long break splits one session into several sections."""
    path = folder / LOG_NAME
    try:
        text, mtime_ns = path.read_text(encoding="utf-8"), path.stat().st_mtime_ns
    except OSError:
        return None
    starts = list(_LOG_SECTION.finditer(text))
    if not starts:
        return None
    number = int(starts[-1].group(1))
    parts = [text[m.start():(starts[i + 1].start() if i + 1 < len(starts) else len(text))]
             for i, m in enumerate(starts) if int(m.group(1)) == number]
    return Turns(number, "\n".join(part.strip() for part in parts), mtime_ns)


def world_date(folder: Path) -> str:
    try:
        cal = json.loads((folder / "calendar.json").read_text(encoding="utf-8"))
        months = cal.get("months") or []
        month = int(cal.get("month", 1))
        name = months[month - 1] if 1 <= month <= len(months) else f"Month {month}"
        return f"{int(cal.get('day', 1))} {name} {int(cal.get('year', 1))}"
    except (OSError, ValueError, AttributeError, TypeError):
        return ""


def _state_location(folder: Path) -> str:
    match = _LOCATION.search(_read(folder / "state.md"))
    return match.group(1).strip("* ") if match else ""


def _speaker(block: dict) -> str:
    if block.get("label"):
        return block["label"]
    kind = block.get("kind")
    if kind in ("player", "npc", "action"):
        return " ".join(str(block.get("name") or "").split()) or "Players"
    return {"dice": "Dice", "tutor": "DM hint"}.get(kind, "DM")


def _body(text: str) -> str:
    # A line starting with "#" would read as a heading to anyone splitting the log by sessions.
    lines = text.strip().splitlines()
    return "\n".join(lines[:1] + ["\\" + line if line.startswith("#") else line for line in lines[1:]])


def _fingerprint(block: dict) -> str:
    raw = json.dumps([_speaker(block), " ".join(block["text"].split())], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _append_turn(folder: Path, blocks: list, place: str, date: str, now: float) -> int:
    """Append one entry; returns the session number it went under."""
    key = str(folder.resolve())
    log = folder / LOG_NAME
    with npc_entries.locked("records-state"):  # one lock: the state file covers every campaign
        state = _load_state()
        mine = state.get(key) if isinstance(state.get(key), dict) else {}
        prints = [_fingerprint(b) for b in blocks]
        recent = mine.get("recent") or []
        new_session = not log.exists() or now - float(mine.get("last") or 0) >= SESSION_GAP_S
        replay = all(b.get("kind") != "award" and len(b["text"].strip()) >= REPLAY_MIN_CHARS for b in blocks)
        if not new_session and replay and all(p in recent for p in prints):
            return int(mine.get("session") or session_number(folder))
        out = "" if log.exists() else LOG_HEADER
        if new_session:
            mine["session"] = session_number(folder)
            out += f"\n## Session {mine['session']} · {time.strftime('%Y-%m-%d', time.localtime(now))}\n"
        heading = " · ".join(part for part in (time.strftime("%H:%M", time.localtime(now)), place, date) if part)
        out += f"\n### {heading}\n\n" + "\n\n".join(f"**{_speaker(b)}:** {_body(b['text'])}" for b in blocks) + "\n"
        with open(log, "a", encoding="utf-8") as f:
            f.write(out)
        mine.update(last=now, place=place, recent=(recent + prints)[-RECENT_KEEP:])
        state[key] = mine
        _save_state(state)
        return int(mine["session"])


def _cast_voice(folder: Path, name: str) -> str:
    import cast_voices
    cast = cast_voices.voices(cast_voices.read_raw(folder / "cast.json"))
    return cast.get(cast_voices.cast_key(cast, name), "")


def _entry_file(folder: Path, name: str) -> "Path | None":
    """The file holding this person's entry: npcs.md, else npcs-full.md, else None."""
    for file_name in ("npcs.md", "npcs-full.md"):
        path = folder / file_name
        if path.is_file() and npc_entries.entry_span(npc_entries.read_lines(path), name, first_word=True):
            return path
    return None


def _npc_file(folder: Path, name: str) -> Path:
    return _entry_file(folder, name) or folder / "npcs.md"


def is_full_name(name: str) -> bool:
    """"Tova Lind" or "Anselm of Ashbridge", not a role label such as "Warden rider", "Auditor" or "The Ferryman"."""
    words = name.split()
    if len(words) < 2 or words[0].casefold() in ("the", "a", "an") or words[-1].casefold() in _ROLE_WORDS:
        return False
    return all(word[:1].isupper() or (i and word.casefold() in _NAME_PARTICLES) for i, word in enumerate(words))


def _met_before(folder: Path, path: Path, name: str, session: int) -> bool:
    """The entry shows an earlier meeting: a note line, an earlier session, or a description the logs back up."""
    lines = npc_entries.read_lines(path)
    span = npc_entries.entry_span(lines, name, first_word=True)
    if not span:
        return False
    described = False
    for line in (raw.strip() for raw in lines[span[0] + 1:span[1]]):
        if not line or line.startswith("|"):
            continue
        if any(int(n) < session for n in _SESSION_MENTION.findall(line)) or line.startswith(("- Party note", "* Party note")):
            return True
        described = described or not _FIELD.match(line)
    if not described:
        return False
    heading = _ANNOTATION.split(lines[span[0]].lstrip("#").strip().strip("*"))[0]
    words = heading.split()
    personal = [w for w in words if w.casefold() not in _TITLES]
    names = {" ".join(words), personal[0] if personal else ""} - {""}
    logs = " ".join(_read(folder / f) for f in ("session-log.md", "session-log-archive.md", "state.md"))
    return any(re.search(rf"(?<![\w'’]){re.escape(n)}(?:['’]s)?(?![\w'’])", logs, re.IGNORECASE) for n in names)


def _has_look(path: Path, name: str) -> bool:
    lines = npc_entries.read_lines(path)
    span = npc_entries.entry_span(lines, name, first_word=True)
    return bool(span) and npc_entries.has_field(lines, span, LOOK_LABELS)


def _add_lines(path: Path, name: str, fields: list, only_existing: bool = False) -> None:
    def change(lines: list) -> bool:
        if only_existing and not npc_entries.entry_span(lines, name, first_word=True):
            return False
        return npc_entries.set_fields(lines, name, fields, replace=False, first_word=True)

    npc_entries.edit(path, change)


def _record_npcs(folder: Path, blocks: list, place: str, date: str, session: int) -> None:
    applied = []
    for name, description in npc_describe.results(folder).items():
        try:
            path = _npc_file(folder, name)
            if not _has_look(path, name):
                _add_lines(path, name, [("As seen", description)], only_existing=True)
            applied.append(name)
        except Exception as error:  # noqa: BLE001 — kept, and tried again on the next send
            npc_describe.log(f"{name}: not added to the NPC file yet: {type(error).__name__}: {error}")
    npc_describe.acknowledge(folder, applied)
    narration = "\n\n".join(b["text"] for b in blocks if b.get("kind") is None)
    seen = set()
    for block in blocks:
        name = " ".join(str(block.get("name") or "").split())
        if block.get("kind") != "npc" or not name[:1].isupper() or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        path = _entry_file(folder, name)
        if path is None and not is_full_name(name):
            continue
        path = path or folder / "npcs.md"
        fields = []
        if not _met_before(folder, path, name, session):
            met = f"{place}, session {session}" if place else f"session {session}"
            fields.append(("Met", f"{met} ({date})" if date else met))
        voice = _cast_voice(folder, name) or block.get("voice") or ""
        if voice:
            fields.append(("Voice", voice))
        if fields:
            _add_lines(path, name, fields)
        if narration and not _has_look(path, name):
            npc_describe.request(folder, name, narration)


def record_send(blocks: list, awards: "list[tuple[str, str]]" = (), place: "str | None" = None,
                now: "float | None" = None) -> "str | None":
    """Log a delivered send and record its NPCs. Returns a one-line problem, or None. Never raises."""
    try:
        folder = campaign_folder()
        entries = [dict(b) for b in blocks if str(b.get("text") or "").strip()]
        entries += [{"kind": "award", "label": label, "text": text} for label, text in awards if text.strip()]
        if folder is None or not entries:
            return None
        now = time.time() if now is None else now
        state = _load_state().get(str(folder.resolve())) or {}
        place = " ".join((place or state.get("place") or _state_location(folder)).split())
        date = world_date(folder)
        session = _append_turn(folder, entries, place, date, now)
        _record_npcs(folder, entries, place, date, session)
        return None
    except Exception as error:  # noqa: BLE001 — the table already has the narration
        return f"turn log or NPC record not written ({type(error).__name__}: {error})"[:300]
