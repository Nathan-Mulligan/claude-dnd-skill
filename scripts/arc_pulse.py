"""Count party turns on the current story beat and steer the DM when the beat stalls.

The portal calls `plan(campaign_dir)` before handing each party reply to the DM, adds the returned
directive to that reply, and calls `commit(campaign_dir, pulse)` once the reply is delivered.
The count lives in <campaign>/arc_pulse.json and resets whenever state.md names a new current beat.

Turns before a nudge come from state.md Session Flags: `steer_after: N` (0 turns it off), otherwise
the `spotlight` dial (dm_led 4, balanced 6, player_led 10).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

SPOTLIGHT_LIMITS = {"dm_led": 4, "balanced": 6, "player_led": 10}
PULSE_FILE = "arc_pulse.json"


@dataclass(frozen=True)
class Beat:
    id: str
    label: str
    what_changes: str
    world_pressure: str


@dataclass(frozen=True)
class Pulse:
    beat: str
    turns: int
    directive: str | None


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def current_beat(state_text: str) -> Beat | None:
    if yaml is None:
        return None
    block = re.search(r"```ya?ml\s*\n(.*?)```", _section(state_text, "Campaign Arc"), re.DOTALL)
    try:
        arc = yaml.safe_load(block.group(1)) if block else None
    except yaml.YAMLError:
        return None
    if not isinstance(arc, dict) or arc.get("type") != "dynamic":
        return None
    beat_id = str(arc.get("current_beat") or "").strip()
    acts = arc.get("acts")
    for act in acts if isinstance(acts, list) else []:
        beats = act.get("beats") if isinstance(act, dict) else None
        for beat in beats if isinstance(beats, list) else []:
            if isinstance(beat, dict) and str(beat.get("id")) == beat_id:
                return Beat(beat_id, str(beat.get("label") or beat_id),
                            str(beat.get("what_changes") or ""), str(beat.get("world_pressure") or ""))
    return None


def turn_limit(state_text: str) -> int:
    flags = _section(state_text, "Session Flags")
    explicit = re.search(r"^\s*steer_after:\s*(\d+)\s*$", flags, re.MULTILINE)
    if explicit:
        return int(explicit.group(1))
    spotlight = re.search(r"^\s*spotlight:\s*(\w+)", flags, re.MULTILINE)
    return SPOTLIGHT_LIMITS.get(spotlight.group(1) if spotlight else "balanced", SPOTLIGHT_LIMITS["balanced"])


def directive(beat: Beat, turns: int) -> str:
    return (
        f"[[DM steering, hidden from players: the story has stayed on beat {beat.id} \"{beat.label}\" for "
        f"{turns} party turns. This turn, resolve the party's action briefly, then bring this beat's world "
        f"pressure into the scene as something that happens now and demands a response: {beat.world_pressure}. "
        f"Aim it at what this beat must change: {beat.what_changes}. Make it concrete: a person arrives, a "
        "deadline lands, a problem breaks open. A hint or rumour is not enough. Offer the party real choices "
        "and never decide for them. When the beat's change has landed, run /dm:dnd arc advance.]]"
    )


def _read_pulse(campaign_dir: Path) -> dict:
    try:
        data = json.loads((campaign_dir / PULSE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def plan(campaign_dir: Path) -> Pulse | None:
    """The pulse after one more party turn, without saving it."""
    try:
        state_text = (campaign_dir / "state.md").read_text(encoding="utf-8")
    except OSError:
        return None
    beat = current_beat(state_text)
    if beat is None:
        return None
    saved = _read_pulse(campaign_dir)
    turns = (saved.get("turns", 0) if saved.get("beat") == beat.id else 0) + 1
    limit = turn_limit(state_text)
    if limit and turns >= limit:
        return Pulse(beat.id, 0, directive(beat, turns))
    return Pulse(beat.id, turns, None)


def commit(campaign_dir: Path, pulse: Pulse) -> None:
    path = campaign_dir / PULSE_FILE
    tmp = path.with_name(PULSE_FILE + ".tmp")
    tmp.write_text(json.dumps({"beat": pulse.beat, "turns": pulse.turns}), encoding="utf-8")
    os.replace(tmp, path)
