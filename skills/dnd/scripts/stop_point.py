#!/usr/bin/env python3
"""Where play stopped, so a fresh DM continues from that exact moment.

  stop_point.py write --campaign X --where "<one line>" [--waiting "<open question or roll>"]
      Reads the last exchanges as JSON on stdin, [{"who": "Players", "text": "..."}, ...],
      oldest first, and writes <campaign>/session-tail.md with the last eight, verbatim.
  stop_point.py show --campaign X
      Prints that note, plus any display lines shown after it was written. Without a note,
      prints the last eight entries of the display's session_tail.json instead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import campaigns_dir  # noqa: E402

HEADING = "# Where we stopped"
MARK = "<!-- display-mark: "
KEEP = 8
TEXT_LIMIT = 3000
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def campaign(name: str) -> Path:
    folder = campaigns_dir() / name
    if not NAME_RE.fullmatch(name) or name in {".", ".."} or not (folder / "state.md").is_file():
        raise SystemExit(f"No campaign named {name!r}.")
    return folder


def one_line(text: str) -> str:
    return " ".join(text.split())


def read_exchanges(raw: str) -> list[dict]:
    try:
        turns = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SystemExit(f"Exchanges must be a JSON list: {error}")
    if not isinstance(turns, list) or not turns:
        raise SystemExit("Exchanges must be a non-empty JSON list.")
    for turn in turns:
        if not (isinstance(turn, dict) and isinstance(turn.get("who"), str) and isinstance(turn.get("text"), str)
                and turn["who"].strip() and turn["text"].strip()):
            raise SystemExit('Each exchange needs "who" and "text".')
    return turns[-KEEP:]


def render(where: str, waiting: str, turns: list[dict]) -> str:
    lines = [HEADING, where, "", f"**Waiting on:** {waiting}", "", "## Last exchanges, oldest first", ""]
    for turn in turns:
        lines += [f"**{one_line(turn['who'])}:** {turn['text'].strip()[:TEXT_LIMIT]}", ""]
    return "\n".join(lines)


def display_entries(folder: Path) -> list[dict]:
    try:
        entries = json.loads((folder / "session_tail.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [entry for entry in (entries if isinstance(entries, list) else [])
            if isinstance(entry, dict) and isinstance(entry.get("text"), str) and entry["text"].strip()]


def fingerprint(entry: dict) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def as_turns(entries: list[dict], keep: int | None = KEEP) -> list[dict]:
    return [{"who": str(entry.get("player") or entry.get("npc") or ("Dice" if entry.get("dice") else "DM")),
             "text": entry["text"]} for entry in (entries[-keep:] if keep else entries)]


def write(folder: Path, where: str, waiting: str, raw: str) -> None:
    where = one_line(where)
    if not where:
        raise SystemExit("--where needs one line saying where play stopped.")
    text = render(where, one_line(waiting) or "nothing", read_exchanges(raw))
    shown = display_entries(folder)
    if shown:
        text += f"{MARK}{fingerprint(shown[-1])} -->\n"
    target = folder / "session-tail.md"
    temporary = target.with_suffix(".md.writing")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)
    print(f"Saved where play stopped to {target}.")


def mark_of(text: str) -> str | None:
    """The fingerprint of the last display line shown when the note was written."""
    mark = re.search(re.escape(MARK) + r"([0-9a-f]{16}) -->", text)
    return mark.group(1) if mark else None


def entries_after(entries: list[dict], mark: str | None) -> list[dict]:
    """Display lines newer than the mark; all of them when the mark has scrolled out or is unknown."""
    prints = [fingerprint(entry) for entry in entries]
    if mark in prints:
        return entries[len(prints) - prints[::-1].index(mark):]
    return entries


def since(text: str, entries: list[dict]) -> str:
    """Display lines shown after the note was written, e.g. when a DM stopped without saving."""
    mark = mark_of(text)
    if not mark or not entries:
        return ""
    newer = entries_after(entries, mark)
    if mark in [fingerprint(entry) for entry in entries]:
        heading = "## Since then, the display showed (newer than the note above; trust these)"
    else:
        heading = "## Since then, the display moved on past its last thirty lines; the latest (trust these)"
    if not newer:
        return ""
    return "\n".join([heading, ""] + [f"**{turn['who']}:** {turn['text'].strip()[:TEXT_LIMIT]}\n" for turn in as_turns(newer)])


def show(folder: Path) -> str:
    try:
        text = (folder / "session-tail.md").read_text(encoding="utf-8")
    except OSError:
        text = ""
    entries = display_entries(folder)
    if text.startswith(HEADING + "\n"):
        newer = since(text, entries)
        return text + ("\n" + newer if newer else "")
    if not entries:
        return "No stop point saved. Rebuild the moment from state.md's Recent Events and Current Situation.\n"
    return render("Not recorded; these are the last lines the display showed.", "not recorded", as_turns(entries))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("write", "show"):
        command = sub.add_parser(name)
        command.add_argument("--campaign", required=True)
    sub.choices["write"].add_argument("--where", required=True)
    sub.choices["write"].add_argument("--waiting", default="")
    args = parser.parse_args()
    folder = campaign(args.campaign)
    if args.command == "write":
        write(folder, args.where, args.waiting, sys.stdin.read())
    else:
        sys.stdout.write(show(folder))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    main()
