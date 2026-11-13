#!/usr/bin/env python3
"""Read and move the campaign arc kept in state.md's ## Campaign Arc YAML block.

    python3 arc.py status  --campaign <name>
    python3 arc.py advance --campaign <name> [--beat ID]
    python3 arc.py revise  --campaign <name> --beat ID [--pressure "..."] [--what-changes "..."] [--steering "..."]

Only the YAML block inside ## Campaign Arc is rewritten; every other byte of state.md is kept.
Comments inside that block are not kept.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

DONE = {"complete", "skipped"}


class ArcError(Exception):
    pass


def section_span(text: str, heading: str) -> tuple[int, int] | None:
    match = re.search(rf"^## {re.escape(heading)}[ \t]*\r?$\n?(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.span(1) if match else None


def section(text: str, heading: str) -> str:
    span = section_span(text, heading)
    return text[span[0]:span[1]] if span else ""


def _block_span(text: str) -> tuple[int, int] | None:
    span = section_span(text, "Campaign Arc")
    if not span:
        return None
    match = re.search(r"```ya?ml[ \t]*\r?\n(.*?)^```", text[span[0]:span[1]], re.MULTILINE | re.DOTALL)
    return (span[0] + match.start(1), span[0] + match.end(1)) if match else None


def read(state_text: str) -> dict | None:
    span = _block_span(state_text)
    if yaml is None or not span:
        return None
    try:
        arc = yaml.safe_load(state_text[span[0]:span[1]])
    except yaml.YAMLError:
        return None
    return arc if isinstance(arc, dict) else None


def write(state_text: str, arc: dict) -> str:
    span = _block_span(state_text)
    if not span:
        raise ArcError("state.md has no ```yaml block under ## Campaign Arc")
    dumped = yaml.safe_dump(arc, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096)
    if "\r\n" in state_text[span[0]:span[1]]:
        dumped = dumped.replace("\n", "\r\n")
    return state_text[:span[0]] + dumped + state_text[span[1]:]


def beats(arc: dict) -> list[tuple[object, dict]]:
    found = []
    acts = arc.get("acts")
    for act in acts if isinstance(acts, list) else []:
        items = act.get("beats") if isinstance(act, dict) else None
        for beat in items if isinstance(items, list) else []:
            if isinstance(beat, dict) and beat.get("id") is not None:
                found.append((act.get("act"), beat))
    return found


def current(arc: dict | None) -> dict | None:
    """The current beat of a dynamic arc, or None."""
    if not arc or arc.get("type") != "dynamic":
        return None
    beat_id = str(arc.get("current_beat") or "").strip()
    for _, beat in beats(arc):
        if str(beat["id"]) == beat_id:
            return beat
    return None


def structured_next(arc: dict | None) -> dict | None:
    """The current chapter's next outstanding key beat of a structured arc, or None."""
    if not arc or arc.get("type") != "structured":
        return None
    detail = arc.get("current_chapter_detail")
    detail = detail if isinstance(detail, dict) else {}
    listed = detail.get("key_beats")
    keys = [str(k) for k in listed if str(k).strip()] if isinstance(listed, list) else []
    outstanding = arc.get("outstanding_beats")
    if isinstance(outstanding, list):
        left = {str(b) for b in outstanding}
        keys = [k for k in keys if k in left]
    if not keys:
        return None
    return {
        "chapter": str(detail.get("id") or arc.get("current_chapter") or "?"),
        "title": str(detail.get("title") or ""),
        "beat": keys[0],
        "source": str(detail.get("source_ref") or arc.get("arc_file") or "arc.md"),
    }


def _find(arc: dict, beat_id: str) -> tuple[int, object, dict]:
    for index, (act, beat) in enumerate(beats(arc)):
        if str(beat["id"]) == beat_id:
            return index, act, beat
    raise ArcError(f"no beat {beat_id!r} in the arc")


def _drop_outstanding(arc: dict, item: str) -> None:
    outstanding = arc.get("outstanding_beats")
    if isinstance(outstanding, list):
        arc["outstanding_beats"] = [b for b in outstanding if str(b) != item]


def advance(arc: dict, beat_id: str | None = None) -> tuple[dict, str]:
    arc = copy.deepcopy(arc)
    if arc.get("type") == "structured":
        nxt = structured_next(arc)
        target = beat_id or (nxt or {}).get("beat")
        if not target:
            raise ArcError("this chapter has no outstanding key beats; read arc.md and move current_chapter")
        if not isinstance(arc.get("outstanding_beats"), list):
            detail = arc.get("current_chapter_detail") or {}
            arc["outstanding_beats"] = list(detail.get("key_beats") or []) if isinstance(detail, dict) else []
        if target not in [str(b) for b in arc["outstanding_beats"]]:
            raise ArcError(f"{target!r} is not in outstanding_beats")
        _drop_outstanding(arc, target)
        after = structured_next(arc)
        tail = f"Next key beat: {after['beat']}" if after else "Chapter's key beats are done; read arc.md and move current_chapter."
        return arc, f"Key beat done: {target}. {tail}"
    if arc.get("type") != "dynamic":
        raise ArcError("advance works on dynamic or structured arcs only")
    target = str(beat_id or arc.get("current_beat") or "").strip()
    index, _, beat = _find(arc, target)
    beat["status"] = "complete"
    _drop_outstanding(arc, target)
    if str(arc.get("current_beat") or "") != target:
        return arc, f"Beat {target} complete. Current beat stays {arc.get('current_beat')}."
    ordered = beats(arc)
    remaining = [(a, b) for a, b in ordered[index + 1:] + ordered[:index] if b.get("status") not in DONE]
    if not remaining:
        arc["current_beat"] = None
        arc["steering_notes"] = ""
        return arc, f"Beat {target} complete. The arc is complete: run /dm:dnd arc new or set type: sandbox."
    act, nxt = remaining[0]
    nxt["status"] = "current"
    arc["current_beat"] = nxt["id"]
    if act is not None:
        arc["current_act"] = act
    arc["steering_notes"] = ""
    return arc, (f"Beat {target} complete. Now on beat {nxt['id']} \"{nxt.get('label', '')}\": "
                 f"{nxt.get('world_pressure', '')}. Write its steering with: arc.py revise --beat {nxt['id']} --steering \"...\"")


def revise(arc: dict, beat_id: str, pressure: str | None = None, what_changes: str | None = None,
           steering: str | None = None, today: str | None = None) -> dict:
    if arc.get("type") != "dynamic":
        raise ArcError("revise works on dynamic arcs only; a structured arc follows its source")
    if not any(v and v.strip() for v in (pressure, what_changes, steering)):
        raise ArcError("give at least one of --pressure, --what-changes or --steering")
    arc = copy.deepcopy(arc)
    _, _, beat = _find(arc, str(beat_id))
    if beat.get("status") in DONE:
        raise ArcError(f"beat {beat_id} is {beat['status']}; completed beats are not revised")
    today = today or datetime.date.today().isoformat()
    changed = []
    if pressure and pressure.strip():
        beat["world_pressure"] = pressure.strip()
        changed.append("pressure")
    if what_changes and what_changes.strip():
        beat["what_changes"] = what_changes.strip()
        changed.append("what changes")
    if steering and steering.strip():
        arc["steering_notes"] = steering.strip()
        changed.append("steering")
    arc["revised"] = today
    log = arc.get("revision_log")
    arc["revision_log"] = (log if isinstance(log, list) else []) + [f"{today}: {beat_id} revised ({', '.join(changed)})"]
    return arc


def status(arc: dict | None) -> str:
    if not arc:
        return "No campaign arc found in state.md."
    if arc.get("type") == "structured":
        nxt = structured_next(arc)
        if not nxt:
            return f"Structured arc, chapter {arc.get('current_chapter')}: no outstanding key beats. Read arc.md and move on."
        return (f"Chapter {nxt['chapter']} \"{nxt['title']}\"\nNext key beat: {nxt['beat']}\nSource: {nxt['source']}\n"
                f"Outstanding: {arc.get('outstanding_beats')}\nSteering: {arc.get('steering_notes') or '-'}")
    beat = current(arc)
    if not beat:
        return f"Arc type {arc.get('type')}: no current beat."
    return (f"Act {arc.get('current_act')}, beat {beat['id']} \"{beat.get('label', '')}\"\n"
            f"What changes: {beat.get('what_changes', '')}\nPressure: {beat.get('world_pressure', '')}\n"
            f"Steering: {arc.get('steering_notes') or '-'}\nOutstanding: {arc.get('outstanding_beats')}")


def _read_state(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _write_state(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".arc-tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "advance", "revise"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--campaign", "-c", required=True)
        if name != "status":
            cmd.add_argument("--beat", required=name == "revise")
        if name == "revise":
            cmd.add_argument("--pressure")
            cmd.add_argument("--what-changes")
            cmd.add_argument("--steering")
    args = parser.parse_args(argv)
    if yaml is None:
        print("arc.py needs PyYAML (pip install pyyaml).", file=sys.stderr)
        return 1
    from paths import find_campaign
    path = find_campaign(args.campaign) / "state.md"
    try:
        text = _read_state(path)
    except OSError:
        print(f"No state.md for campaign {args.campaign}.", file=sys.stderr)
        return 1
    arc = read(text)
    try:
        if args.command == "status":
            print(status(arc))
            return 0
        if arc is None:
            raise ArcError("no readable ## Campaign Arc YAML in state.md")
        if args.command == "advance":
            arc, message = advance(arc, args.beat)
        else:
            arc, message = revise(arc, args.beat, args.pressure, args.what_changes, args.steering), f"Beat {args.beat} revised."
        _write_state(path, write(text, arc))
    except ArcError as error:
        print(f"arc.py: {error}", file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
