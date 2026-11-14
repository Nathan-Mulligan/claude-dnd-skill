"""sidebar_sync.py — the sidebar's quests and factions, read from the campaign's state.md.

The display checks state.md every few seconds and shows what these two party-known sections say,
so the sidebar never depends on the DM remembering a push:

  ## Active Quests                one top-level bullet per quest
  - **Wagon rental** (active): pay the 3 sp rental at the next market.
  - [resolved] Deliver the parcel.          status: active (default), threat, resolved, failed
  - Collect the hoops from the forge; ...   no bold name: a short name is cut from the text

  ## Faction Standings            one bullet per faction the party knows
  - **Road Wardens**: Suspicious — they doubled the toll.
  - Millers' Compact: Friendly

A missing section leaves that list as the DM last pushed it. Only these two sections are read;
both are in lore_sync.PUBLIC_SECTIONS, so DM-only sections never reach the screen.
"""
from __future__ import annotations

import os
import re
import stat

SECTIONS = {"quests": "active quests", "factions": "faction standings"}
STATUSES = {"active": "active", "threat": "threat", "resolved": "resolved", "failed": "failed",
            "done": "resolved", "complete": "resolved", "completed": "resolved"}
NAME_MAX = 60
STATE_MAX_BYTES = 1 << 20

_BOLD = re.compile(r"^\*\*(.+?)\*\*\s*")
_STATUS_TAG = re.compile(r"^[\[(]\s*([A-Za-z]+)\s*[\])]\s*")
_SEPARATOR = re.compile(r"^[:.;—–-]\s*")
_STANDING_END = re.compile(r"\s[—–-]\s|[;.]\s|\(")  # no open-ended whitespace runs: parsing stays linear
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


def read_state(path, limit: int = STATE_MAX_BYTES) -> "str | None":
    """A file's text, or None. A sandboxed DM can write the campaign and runtime folders, so a link,
    anything but a plain file (a FIFO would hang the reader) or a file over `limit` bytes is never read."""
    if not _NOFOLLOW and os.path.islink(path):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return None
    finally:
        os.close(fd)


def _sections(text: str) -> dict:
    """{key: [top-level bullet texts]} for each of SECTIONS present."""
    found, key = {}, None
    for line in text.splitlines():
        if line.startswith(("# ", "## ", "##\t")):
            title = line[2:].strip().lower() if line.startswith("##") else ""
            key = next((k for k, s in SECTIONS.items() if title.startswith(s)), None)
            if key:
                found.setdefault(key, [])
        elif key and line.startswith(("- ", "* ")) and line[2:].strip():
            found[key].append(line[2:].strip())
    return found


def _status(text: str) -> "tuple[str | None, str]":
    tag = _STATUS_TAG.match(text)
    if tag and tag.group(1).lower() in STATUSES:
        return STATUSES[tag.group(1).lower()], text[tag.end():]
    return None, text


def _trailing_status(text: str) -> "tuple[str | None, str]":
    text = text.rstrip()
    if text.endswith((")", "]")):
        start = text.rfind("(" if text.endswith(")") else "[")
        word = text[start + 1:-1].strip().lower() if start >= 0 else ""
        if word in STATUSES:
            return STATUSES[word], text[:start]
    return None, text


def _short(text: str) -> str:
    head = re.split(r";|:|\s[—–]\s|(?<=[.!?])\s", text, maxsplit=1)[0].strip()
    if len(head) <= NAME_MAX:
        return head
    cut = head[:NAME_MAX].rsplit(" ", 1)[0].rstrip(",;:—– ")
    return cut + "…"


def _item(name: str, key: str, value: str, detail: str) -> dict:
    item = {"name": name, key: value}
    if detail:
        item["detail"] = detail
    return item


def quest(text: str) -> "dict | None":
    status, text = _status(text)
    bold = _BOLD.match(text)
    if bold:
        name = bold.group(1).strip().rstrip(":")
        rest = text[bold.end():]
        for _ in range(2):  # "(resolved): ..." or ": [resolved] ..."
            tagged, rest = _status(_SEPARATOR.sub("", rest, count=1))
            status = status or tagged
        detail = rest.strip()
    else:
        if status is None:
            status, text = _trailing_status(text)
        text = text.strip()
        name = _short(text)
        detail = text if name != text else ""
    return _item(name, "status", status or "active", detail) if name else None


def faction(text: str) -> "dict | None":
    bold = _BOLD.match(text)
    if bold:
        name, rest = bold.group(1).strip().rstrip(":"), _SEPARATOR.sub("", text[bold.end():], count=1).strip()
    elif ":" in text:
        name, rest = (part.strip() for part in text.split(":", 1))
    else:
        return None
    end = _STANDING_END.search(rest)
    standing = (rest[:end.start()] if end else rest).strip().rstrip(".;")
    detail = rest[end.end():].strip() if end else ""
    if end and end.group().strip() == "(":
        detail = detail.removesuffix(")").strip()
    if not name or not standing:
        return None
    return _item(name, "standing", standing, detail)


def lists(state_md: str) -> dict:
    """{"quests": [...], "factions": [...]}, each only when its section is in state.md."""
    parse = {"quests": quest, "factions": faction}
    return {key: [item for item in map(parse[key], bullets) if item]
            for key, bullets in _sections(state_md).items()}


def merged(shown: list, derived: list) -> "list | None":
    """The list to show, or None when the sidebar already shows it. An item without its own detail
    keeps the note already shown for that name (the notes helper's or the DM's)."""
    notes = {str(item.get("name")): item.get("detail") for item in shown if isinstance(item, dict)}
    new = [dict(item, detail=notes[item["name"]]) if not item.get("detail") and notes.get(item["name"]) else item
           for item in derived]
    return None if new == shown else new
