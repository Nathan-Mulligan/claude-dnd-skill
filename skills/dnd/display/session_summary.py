#!/usr/bin/env python3
"""session_summary.py — the notes helper writes the Save & close summary.

At Save & close the portal first asks the DM for the judgement fields in
state.md (in-world date, Active Quests, Clocks, Next Hooks), the character
sheets, the party roster and the stop point. It then calls `prepare()`, which
asks the helper chosen in the portal (lore_sync's choice, Luna by default) for:
  * this session's `### Recap` and `### Key Events` in session-log.md;
  * Continuity Archive bullets for each session-log entry older than the
    newest two, which then moves whole to session-log-archive.md;
  * a new `## Story So Far` when Continuity Archive entries older than the
    newest five fold into it. Their bullets move to session-log-archive.md.
`prepare()` writes nothing. The portal calls `write()` while the DM waits
idle, after checking it changed nothing since its save, so each file has one
writer at a time. Any failure raises; the portal then asks the DM to write the
summary itself before stopping it, and logs why to runtime/session_summary.log.

The helper sees only party-facing material: this session's log entry, this
session's turns word for word (from turn-log.md, which send.py keeps; else the
display lines since the last saved stop point, from the display's text log),
the new stop point, Recent Events, the last recap and the archive text being
moved or folded. `### DM ...` subsections and lines that read as DM-only are
stripped. A reply naming anyone or anything, or giving a number, that the
material never mentions is refused.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "scripts"))
import lore_sync  # noqa: E402
import records  # noqa: E402
import stop_point  # noqa: E402
from runtime_paths import rt  # noqa: E402

KEEP_LOG_ENTRIES = 2
KEEP_ARCHIVE = 5
MAX_ROTATE = 4  # a campaign with a long unarchived log catches up over several closes
MAX_FOLD = 10
STORY_WORDS = 150
SUMMARY_TIMEOUT_S = 240  # under the display's five-minute input pause
EFFORT = "medium"
SOURCE_CHARS = 60000
TRANSCRIPT_CHARS = 150000
FILES = ("session-log-archive.md", "session-log.md", "state.md")  # write order: additions land first

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["recap", "key_events", "archived", "story_so_far"],
    "properties": {
        "recap": {"type": "string"},
        "key_events": {"type": "array", "items": {"type": "string"}},
        "archived": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["session", "label", "bullets"],
                "properties": {
                    "session": {"type": "integer"},
                    "label": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "story_so_far": {"type": "string"},
    },
}

LOG_HEADING = re.compile(r"^## Session (\d+)\b([^\n]*)", re.M)
ARCHIVE_HEADING = re.compile(r"^### Session (\d+)\b([^\n]*)", re.M)
LEVEL_2 = re.compile(r"^## ", re.M)
LEVEL_2_OR_3 = re.compile(r"^#{2,3} ", re.M)
SUBSECTION_END = re.compile(r"^(?:#{2,3} |---[ \t]*$)", re.M)
PLACEHOLDER = re.compile(r"[-—]?\s*(?:<[^>]*>|\*\([^)]*\)\*|none|tbd)?", re.I)
DM_ONLY = re.compile(r"^### [^\n]*\bDM\b", re.M)
# Lines in moving archive text that may hold what only the DM knows. They still move whole to
# session-log-archive.md; they are just never shown to the helper.
DM_LINE = re.compile(r"\b(?:DM|GM)\b|\bsecretly\b|\bsecret\s*[:)\]]|\(\s*secret|hidden from|off-?screen"
                     r"|unknown to the party|calibration|faction move|party (?:does not|doesn't|did not|didn't) know",
                     re.I)
NAME = re.compile(r"\b[A-Z][A-Za-z'’-]*[A-Za-z]")
# Capitalised only because they open a sentence; never treated as names.
OPENERS = set("""a after afterwards again also an and as at back before but by during each eventually
finally first for from he her here his however in instead into it its later meanwhile next now of on once
or our she so soon still that the their then there these they this those to together under we when while
with without yet you your""".split())


class Block(NamedTuple):
    number: int
    rest: str  # the heading after the number, e.g. " — 2026-11-01"
    start: int
    end: int
    text: str


def helper() -> "tuple[str, str] | None":
    """The helper chosen in the portal, or None when it is off."""
    return lore_sync.choose_model()


def log(message: str) -> None:
    try:
        with open(rt("session_summary.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    except OSError:
        pass


def _read(path: Path) -> "str | None":
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


# ── markdown ────────────────────────────────────────────────────────────────

def blocks(text: str, heading: re.Pattern, stop: re.Pattern, offset: int = 0) -> list[Block]:
    found = []
    for m in heading.finditer(text):
        nxt = stop.search(text, m.end())
        end = nxt.start() if nxt else len(text)
        found.append(Block(int(m.group(1)), m.group(2).rstrip(), offset + m.start(), offset + end, text[m.start():end]))
    return found


def section(text: str, title: str) -> "tuple[int, int] | None":
    """(body start, end) of a `## Title` section."""
    m = re.search(rf"^## {re.escape(title)}\b[^\n]*\n?", text, re.M)
    if not m:
        return None
    nxt = LEVEL_2.search(text, m.end())
    return m.end(), (nxt.start() if nxt else len(text))


def subsection(text: str, title: str) -> "tuple[int, int] | None":
    """(body start, body end) of a `### Title` subsection."""
    m = re.search(rf"^### {re.escape(title)}[ \t]*\n?", text, re.M)
    if not m:
        return None
    nxt = SUBSECTION_END.search(text, m.end())
    return m.end(), (nxt.start() if nxt else len(text))


def filled(body: str) -> bool:
    return any(not PLACEHOLDER.fullmatch(line.strip()) for line in body.splitlines())


def has_recap(entry: str) -> bool:
    span = subsection(entry, "Recap")
    return bool(span) and filled(entry[span[0]:span[1]])


def without_dm_only(text: str, lines: bool = True) -> str:
    """Drop `### DM Calibration`, `### DM Notes` and the like, and (in archive text) lines that read as DM-only."""
    while (m := DM_ONLY.search(text)):
        nxt = SUBSECTION_END.search(text, m.end())
        text = text[:m.start()] + (text[nxt.start():] if nxt else "")
    return "\n".join(line for line in text.splitlines() if not (lines and DM_LINE.search(line)))


def splice(text: str, edits: list) -> str:
    for start, end, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def put_subsection(entry: str, title: str, body: str, replace: bool, after: "str | None" = None) -> str:
    span = subsection(entry, title)
    if span:
        if replace or not filled(entry[span[0]:span[1]]):
            return entry[:span[0]] + body + "\n\n" + entry[span[1]:].lstrip("\n")
        return entry
    anchor = subsection(entry, after) if after else None
    if anchor:
        at = anchor[1]
    else:
        at = entry.index("\n") + 1 if "\n" in entry else len(entry)
        if entry[at:].startswith("**"):  # keep the **Location:** line under the heading
            nl = entry.find("\n", at)
            at = len(entry) if nl < 0 else nl + 1
    head = entry[:at] if entry[:at].endswith("\n") else entry[:at] + "\n"
    return head.rstrip("\n") + "\n\n" + f"### {title}\n{body}\n\n" + entry[at:].lstrip("\n")


def bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def clip(text: str, limit: int = SOURCE_CHARS) -> str:
    """Keep the start and the end of a long source; the middle is the least needed."""
    text = text.strip()
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n[…]\n" + text[-half:]


# ── what the helper is shown ────────────────────────────────────────────────

def display_mark(folder: Path) -> "str | None":
    """Read before the DM rewrites the stop point: display lines after this mark are this session's."""
    return stop_point.mark_of(_read(folder / "session-tail.md") or "")


def transcript(campaign: str) -> list[dict]:
    """The display's durable text log (its last 2000 lines), this campaign's lines only."""
    try:
        with open(rt("text_log.json"), encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError):
        return []
    return [e for e in (entries if isinstance(entries, list) else [])
            if isinstance(e, dict) and e.get("_camp") == campaign and isinstance(e.get("text"), str)
            and e["text"].strip() and not e.get("tutor")]


def display_lines(folder: Path, mark: "str | None") -> list[str]:
    """Everything the display showed since the last saved stop point; the 30-line tail if the log is missing."""
    entries = stop_point.entries_after(transcript(folder.name) or stop_point.display_entries(folder), mark)
    return [f"{turn['who']}: {' '.join(turn['text'].split())}" for turn in stop_point.as_turns(entries, keep=None)]


def stop_note(folder: Path) -> str:
    text = _read(folder / "session-tail.md") or ""
    text = re.sub(re.escape(stop_point.MARK) + r"[0-9a-f]{16} -->\n?", "", text)
    return text.replace(stop_point.HEADING + "\n", "", 1).strip()


class Material(NamedTuple):
    log: str
    state: str
    archive_file: str
    current: Optional[Block]
    number: int
    rotate: list[Block]
    archive: list[Block]
    fold: list[Block]
    lines: list[str]


def turn_lines(text: str) -> list[str]:
    """turn-log.md paragraphs as `Speaker: text` lines, without the tutor's hints (party-facing, not story)."""
    lines = []
    for para in re.split(r"\n\s*\n", text):
        para = " ".join(para.split())
        if para and not para.startswith(("## Session ", "**DM hint:**")):
            lines.append(re.sub(r"^\*\*(.+?):\*\* ", r"\1: ", para))
    return lines


def this_session(folder: Path, log_text: str, archive_file: str, state: str,
                 entries: list[Block]) -> "tuple[Optional[Block], int, Optional[records.Turns]]":
    """This session's open session-log.md entry (if any), its number, and its turns from turn-log.md.

    send.py numbers a turn-log.md session when it starts, so while that log runs ahead of
    session-log.md and has turns newer than the last summary, its number is this session's,
    and an entry with that number (even one the DM recapped mid-session) is filled in place.
    Otherwise this session is the newest entry without a Recap, or a new one."""
    turns = records.latest_session(folder)
    by_number = {b.number: b for b in entries}
    if turns and turns.number >= max(by_number, default=-1):
        entry = by_number.get(turns.number)
        log_file = folder / "session-log.md"
        if (entry is None or not has_recap(entry.text) or not log_file.exists()
                or turns.mtime_ns > log_file.stat().st_mtime_ns):
            return entry, turns.number, turns
    newest = max(entries, key=lambda b: b.number, default=None)
    current = newest if newest and not has_recap(newest.text) else None
    return current, (current.number if current else records.next_session(log_text, archive_file, state)), None


def session_number(folder: Path) -> int:
    """The session the summary goes under: the `## Session N` a DM writing it by hand should use."""
    folder = Path(folder)
    log_text = _read(folder / "session-log.md") or ""
    return this_session(folder, log_text, _read(folder / "session-log-archive.md") or "",
                        _read(folder / "state.md") or "", blocks(log_text, LOG_HEADING, LEVEL_2))[1]


def gather(folder: Path, mark: "str | None") -> Material:
    state = _read(folder / "state.md") or ""
    log_text = _read(folder / "session-log.md") or ""
    archive_file = _read(folder / "session-log-archive.md") or ""
    entries = blocks(log_text, LOG_HEADING, LEVEL_2)
    current, number, turns = this_session(folder, log_text, archive_file, state, entries)
    older = sorted((b for b in entries if b is not current), key=lambda b: b.number)
    rotate = older[:max(0, len(older) + 1 - KEEP_LOG_ENTRIES)][:MAX_ROTATE]
    span = section(state, "Continuity Archive")
    archive = sorted(blocks(state[span[0]:span[1]], ARCHIVE_HEADING, LEVEL_2_OR_3, span[0]) if span else [],
                     key=lambda b: b.number)
    overflow = max(0, len(archive) + len(rotate) - KEEP_ARCHIVE)
    return Material(log_text, state, archive_file, current, number, rotate, archive,
                    archive[:min(overflow, MAX_FOLD)],
                    turn_lines(turns.text) if turns else display_lines(folder, mark))


def story_so_far(state: str) -> str:
    span = section(state, "Story So Far")
    body = state[span[0]:span[1]].strip() if span else ""
    return body if filled(body) else ""


def recent_events(state: str) -> str:
    span = section(state, "Recent Events")
    return state[span[0]:span[1]].strip() if span else ""


def last_recap(log_text: str, current: "Block | None") -> str:
    this = current.number if current else None  # blocks() builds new objects, so compare by number
    done = [b for b in blocks(log_text, LOG_HEADING, LEVEL_2) if b.number != this and has_recap(b.text)]
    if not done:
        return ""
    entry = max(done, key=lambda b: b.number).text
    span = subsection(entry, "Recap")
    return entry[span[0]:span[1]].strip()


def build_prompt(m: Material, folder: Path) -> str:
    this_log = ""
    if m.current:
        body = without_dm_only(m.current.text, lines=False)
        this_log = body[body.index("\n") + 1:] if "\n" in body else ""
    parts = [
        f"LAST SESSION'S RECAP (already recorded; never repeat it as this session's events):\n{last_recap(m.log, m.current) or '(none)'}",
        f"THIS SESSION'S LOG (Session {m.number}):\n{clip(this_log) or '(none)'}",
        "WHAT THE TABLE WAS SHOWN THIS SESSION, OLDEST FIRST:\n" + (clip("\n".join(m.lines), TRANSCRIPT_CHARS) or "(none)"),
        f"WHERE PLAY STOPPED (saved by the DM at the end of this session):\n{clip(stop_note(folder)) or '(none)'}",
        f"RECENT EVENTS (from the campaign notes; may reach back before this session):\n{clip(recent_events(m.state), 8000) or '(none)'}",
    ]
    if m.rotate:
        parts.append("OLDER SESSIONS TO ARCHIVE:\n" + "\n\n".join(
            f"Session {b.number}:\n{clip(without_dm_only(b.text), 12000)}" for b in m.rotate))
        archived = ('one object per session under OLDER SESSIONS TO ARCHIVE ('
                    + ", ".join(str(b.number) for b in m.rotate)
                    + '), each with "session" (its number), "label" (a few words: the main place or event) and '
                    '"bullets" (3 to 5 facts from that session most likely to come back: names, promises, '
                    'items, decisions, roll outcomes that changed the story).')
    else:
        archived = "an empty list; there are no older sessions to archive."
    if m.fold:
        parts.append(f"STORY SO FAR (current):\n{story_so_far(m.state) or '(none yet)'}")
        parts.append("SESSIONS FOLDING INTO THE STORY SO FAR:\n" + "\n\n".join(
            clip(without_dm_only(b.text), 6000) for b in m.fold))
        story = ("one paragraph of at most 130 words that retells STORY SO FAR (the oldest events) followed by "
                 "SESSIONS FOLDING INTO THE STORY SO FAR, in that order. Use only those two sections for this "
                 "field: nothing from this session, the last recap, the recent events or the older sessions to "
                 "archive, which are kept elsewhere. Keep the people, places and promises that matter.")
    else:
        story = "an empty string."
    return f"""You keep the written record of a tabletop role-playing campaign. The next game starts with a
fresh Dungeon Master who knows only these notes, so they must be accurate.

Rules:
- Use only the material below. Never add events, names, places, motives, outcomes or guesses it does not state.
- Write what the party saw, did, said and learned. Leave out anything meant only for the Dungeon Master.
- Past tense, third person, plain sentences, names spelled as the material spells them.
- Do not run commands.

Reply with a JSON object:
- "recap": 3 to 5 sentences on what happened in THIS SESSION only, in order, ending with where play stopped and what is waiting.
- "key_events": 3 to 6 short facts from this session that may matter later: names met, promises, prices, items gained or lost, rolls that changed the story.
- "archived": {archived}
- "story_so_far": {story}

{chr(10).join(part + chr(10) for part in parts)}"""


# ── the reply ───────────────────────────────────────────────────────────────

def ask(prompt: str, choice: "tuple[str, str]") -> object:
    provider, model = choice
    return lore_sync._PROVIDERS[provider](prompt, model, schema=SCHEMA, effort=EFFORT, timeout=SUMMARY_TIMEOUT_S)


def one_line(text: object) -> str:
    if not isinstance(text, str):
        raise ValueError("helper reply has a non-text field")
    return " ".join(text.split())


def invented(texts: list[str], source: str) -> list[str]:
    words = {w.lower() for w in re.findall(r"[A-Za-z'’-]+", source)}
    known = words | OPENERS
    # "Tull’s" in the material names Tull exactly; the plural leeway applies only to words as written.
    owners = {re.sub(r"['’]s$", "", w) for w in words}
    unknown = []
    for text in texts:
        for word in NAME.findall(text):
            word = re.sub(r"['’][sS]$", "", word)
            low = word.lower()
            if low not in owners and not {low, low.rstrip("s"), low + "s"} & known and word not in unknown:
                unknown.append(word)
    return unknown


def check(reply: object, m: Material, prompt: str) -> dict:
    if not isinstance(reply, dict) or set(reply) != set(SCHEMA["required"]):
        raise ValueError("helper reply is not {recap, key_events, archived, story_so_far}")
    recap = one_line(reply["recap"])
    if not recap:
        raise ValueError("helper reply has no recap")
    if not isinstance(reply["key_events"], list) or not isinstance(reply["archived"], list):
        raise ValueError("helper reply has a malformed list")
    events = [e for e in (one_line(e) for e in reply["key_events"]) if e][:8]
    archived = {}
    for item in reply["archived"]:
        if not isinstance(item, dict) or not isinstance(item.get("session"), int) or not isinstance(item.get("bullets"), list):
            raise ValueError("helper reply has a malformed archive entry")
        points = [p for p in (one_line(p) for p in item["bullets"]) if p][:6]
        if points:
            archived[item["session"]] = (one_line(item.get("label", "")), points)
    wanted = {b.number for b in m.rotate}
    if set(archived) != wanted:
        missing = sorted(wanted - set(archived)) or sorted(set(archived) - wanted)
        raise ValueError("helper reply archives the wrong sessions: " + ", ".join(f"Session {n}" for n in missing))
    story = one_line(reply["story_so_far"]) if m.fold else ""
    if m.fold and not story:
        raise ValueError("helper reply has no Story So Far for the sessions folding into it")
    if len(story.split()) > STORY_WORDS:
        raise ValueError(f"helper Story So Far is over {STORY_WORDS} words")
    texts = [recap, story] + events + [t for label, points in archived.values() for t in [label] + points]
    names = invented(texts, prompt)
    if names:
        raise ValueError("helper reply names what the material never mentions: " + ", ".join(names[:5]))
    numbers = set(re.findall(r"\d+", prompt))
    new = [n for t in texts for n in re.findall(r"\d+", t) if n not in numbers]
    if new:
        raise ValueError("helper reply gives numbers the material never gives: " + ", ".join(sorted(set(new))[:5]))
    return {"recap": recap, "events": events, "archived": archived, "story": story}


# ── the new files ───────────────────────────────────────────────────────────

def new_log(m: Material, reply: dict, today: str) -> str:
    edits = [(b.start, b.end, "") for b in m.rotate]
    events = bullets(reply["events"]) or "-"
    if m.current:
        # A Recap already there was written mid-session; its Key Events give way to the whole session's.
        resummarised = has_recap(m.current.text)
        entry = put_subsection(m.current.text, "Recap", reply["recap"], replace=True)
        entry = put_subsection(entry, "Key Events", events, replace=resummarised, after="Recap")
        edits.append((m.current.start, m.current.end, entry))
        return splice(m.log, edits)
    text = splice(m.log, edits)
    text = text.rstrip("\n") + "\n\n" if text.strip() else ""
    return text + f"## Session {m.number} — {today}\n\n### Recap\n{reply['recap']}\n\n### Key Events\n{events}\n"


def new_state(m: Material, reply: dict) -> str:
    state, edits = m.state, []
    entries = "".join(
        f"### Session {b.number}{b.rest}{' — ' + reply['archived'][b.number][0] if reply['archived'][b.number][0] else ''}\n"
        f"{bullets(reply['archived'][b.number][1])}\n\n" for b in m.rotate)
    span = section(state, "Continuity Archive")
    if entries and span:
        body_end = span[1]
        tail = state[:body_end].rstrip("\n")
        edits.append((len(tail), body_end, "\n\n" + entries))
    elif entries:
        story = section(state, "Story So Far")
        recent = re.search(r"^## Recent Events\b", state, re.M)
        at = story[1] if story else (recent.start() if recent else len(state))
        before = state[:at].rstrip("\n")
        edits.append((len(before), at, ("\n\n" if before else "") + "## Continuity Archive\n\n" + entries))
    edits += [(b.start, b.end, "") for b in m.fold]
    if m.fold:
        story = section(state, "Story So Far")
        if story:
            edits.append((story[0], story[1], reply["story"] + "\n\n"))
        else:
            at = state.index("## Continuity Archive")
            edits.append((at, at, f"## Story So Far\n{reply['story']}\n\n"))
    return splice(state, edits)


def new_archive_file(m: Material) -> str:
    moved = [b.text.strip() for b in m.rotate] + [b.text.strip() for b in m.fold]
    if not moved:
        return m.archive_file
    head = m.archive_file.rstrip("\n") + "\n\n" if m.archive_file.strip() else ""
    return head + "\n\n".join(moved) + "\n"


def prepare(folder: Path, mark: "str | None", choice: "tuple[str, str]", today: "str | None" = None) -> dict:
    """Ask the helper and return {file name: (text it was built from, new text)}. Writes nothing."""
    m = gather(Path(folder), mark)
    if not m.current and not m.lines:
        log(f"{Path(folder).name}: nothing new this session; summary left as it was")
        return {}
    prompt = build_prompt(m, Path(folder))
    reply = check(ask(prompt, choice), m, prompt)
    today = today or time.strftime("%Y-%m-%d")
    plan = {
        "session-log.md": (_read(Path(folder) / "session-log.md"), new_log(m, reply, today)),
        "state.md": (m.state, new_state(m, reply)),
    }
    if m.rotate or m.fold:
        plan["session-log-archive.md"] = (_read(Path(folder) / "session-log-archive.md"), new_archive_file(m))
    log(f"{Path(folder).name}: Session {m.number} summary by {choice[0]}/{choice[1]} "
        f"({len(m.rotate)} archived, {len(m.fold)} folded)")
    return plan


def _replace(folder: Path, name: str, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            os.chmod(tmp, (folder / name).stat().st_mode & 0o777)
        except FileNotFoundError:
            os.chmod(tmp, 0o644)
        os.replace(tmp, folder / name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write(folder: Path, plan: dict) -> None:
    """Write the planned files, refusing if any changed since they were read. If one write fails,
    the files already written are put back, so the DM's fallback starts from the saved state."""
    folder = Path(folder)
    for name, (base, _) in plan.items():
        if _read(folder / name) != base:
            raise ValueError(f"{name} changed after the summary was prepared")
    written = []
    try:
        for name in FILES:
            if name not in plan or plan[name][0] == plan[name][1]:
                continue
            _replace(folder, name, plan[name][1])
            written.append(name)
    except BaseException:
        for name in reversed(written):
            base = plan[name][0]
            try:
                if base is None:
                    (folder / name).unlink(missing_ok=True)
                else:
                    _replace(folder, name, base)
            except OSError as error:
                log(f"{folder.name}: could not put back {name} after a failed summary write: {error}")
        raise
