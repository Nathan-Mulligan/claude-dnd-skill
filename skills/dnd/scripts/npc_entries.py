"""Entries in a campaign's npcs.md: a `##`/`###` heading per person, then `- **Label:** value` lines.

Every script that writes npcs.md (oracle.py, and send.py through display/records.py) edits it
through `edit()`, which holds one lock per file, so two scripts never lose each other's lines.
"""
from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from paths import runtime_dir

try:
    import fcntl
except ImportError:  # Windows has no flock; writes there go unlocked, as before
    fcntl = None

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)")
_SUBSECTIONS = {"personality", "relationships", "notes"}
_ANNOTATION_STARTS = (" (", " —", " –", " - ", ",", ":")


def norm_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def _heading_is(text: str, name: str, exact: bool) -> bool:
    """Exact name, or (exact=False) the name followed by an annotation such as "(trader)" or "— innkeeper"."""
    heading, wanted = norm_name(text.strip("* ")), norm_name(name)
    return heading == wanted or (not exact and heading.startswith(tuple(wanted + a for a in _ANNOTATION_STARTS)))


def _is_entry_heading(match) -> bool:
    return match.group(2).strip("* ").casefold() not in _SUBSECTIONS


def _span_from(lines: list, i: int) -> tuple:
    level = len(HEADING_RE.match(lines[i]).group(1))
    for j in range(i + 1, len(lines)):
        h = HEADING_RE.match(lines[j])
        if h and len(h.group(1)) <= level and _is_entry_heading(h):
            return i, j
    return i, len(lines)


def _entry_headings(lines: list):
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and 2 <= len(m.group(1)) <= 4 and _is_entry_heading(m) and "<" not in m.group(2):
            yield i, m.group(2)


def entry_span(lines: list, name: str, first_word: bool = False):
    """(start, end) line span of the person's entry, or None.

    With first_word, a name with no entry of its own finds the one entry whose first word it is
    ("Hessa" finds "Hessa Varn"), as the display's voice list does.
    """
    for exact in (True, False):
        for i, heading in _entry_headings(lines):
            if _heading_is(heading, name, exact):
                return _span_from(lines, i)
    if first_word and len(name.split()) == 1:
        wanted = norm_name(name)
        found = [i for i, heading in _entry_headings(lines)
                 if norm_name(heading.strip("* ")).split(" ")[0] == wanted]
        if len(found) == 1:
            return _span_from(lines, found[0])
    return None


def ensure_entry(lines: list, name: str, first_word: bool = False):
    span = entry_span(lines, name, first_word)
    if span:
        return span
    levels = {len(m.group(1)) for m in map(HEADING_RE.match, lines) if m and _is_entry_heading(m)}
    marker = "##" if 2 in levels and 3 not in levels else "###"
    while lines and not lines[-1].strip():
        lines.pop()
    lines += ["", f"{marker} {name}"]
    return len(lines) - 1, len(lines)


def field_line(lines: list, span: tuple, label: str) -> "int | None":
    prefix = f"- **{label}:**"
    return next((k for k in range(span[0] + 1, span[1]) if lines[k].startswith(prefix)), None)


def has_field(lines: list, span: tuple, labels) -> bool:
    return any(field_line(lines, span, label) is not None for label in labels)


def set_fields(lines: list, name: str, fields, replace: bool = True, first_word: bool = False) -> bool:
    """Put `- **Label:** value` lines in the entry, after its first run of bullets.

    replace=False only adds labels the entry lacks, so a line already there, whoever wrote it, stays.
    Returns True when anything changed.
    """
    before = len(lines)
    start, end = ensure_entry(lines, name, first_word)
    changed = len(lines) != before
    insert_at = start + 1
    while insert_at < end and BULLET_RE.match(lines[insert_at]):
        insert_at += 1
    for label, value in fields:
        line = f"- **{label}:** {value}"
        existing = field_line(lines, (start, end), label)
        if existing is None:
            lines.insert(insert_at, line)
            insert_at += 1
            end += 1
            changed = True
        elif replace and lines[existing] != line:
            lines[existing] = line
            changed = True
    return changed


def read_lines(path: Path) -> list:
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else ["# NPCs"]


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def locked(key: str):
    """Hold an exclusive lock named after `key` until the block ends (the OS drops it if we die)."""
    folder = runtime_dir() / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    fd = os.open(folder / f"{digest}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def edit(path: Path, change: Callable[[list], bool]) -> bool:
    """Re-read the file under its lock, apply `change` to its lines, write it back if it changed."""
    path = Path(path)
    with locked(str(path.resolve())):
        lines = read_lines(path)
        if not change(lines):
            return False
        write_atomic(path, "\n".join(lines) + "\n")
        return True
