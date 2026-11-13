"""Count party turns on the current story beat and steer the DM when the beat stalls.

The portal calls `plan(campaign_dir, party_input)` before handing each party reply to the DM, puts the
returned directive in that turn's card, and calls `commit(campaign_dir, pulse)` once the reply is delivered.
The count lives in <campaign>/arc_pulse.json and resets whenever the current beat, its pressure or its
steering changes. Turns during active combat, dice-only replies and out-of-character notes are not counted.

Turns before a nudge come from state.md Session Flags: `steer_after: N` (0 turns it off), otherwise
the `spotlight` dial (dm_led 4, balanced 6, player_led 10). Flags may be written as `- key: value`.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SPOTLIGHT_LIMITS = {"dm_led": 4, "balanced": 6, "player_led": 10}
PULSE_FILE = "arc_pulse.json"
ARC_SCRIPT = "skills/dnd/scripts/arc.py"


def _load_arc():
    # Loaded by path: putting skills/dnd/scripts on sys.path would let its calendar.py shadow the stdlib.
    name = "dnd_arc"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / ARC_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


arc = _load_arc()
section = arc.section


@dataclass(frozen=True)
class Pulse:
    beat: str
    version: str
    turns: int
    nudges: int
    directive: str | None


def clean(text: object) -> str:
    return " ".join(str(text or "").replace("**", "").split())


def field(text: str, heading: str, label: str) -> str:
    match = re.search(rf"^\s*[-*]?\s*\**{re.escape(label)}:?\**:?[ \t]*(.*)$", section(text, heading), re.MULTILINE | re.IGNORECASE)
    return clean(match.group(1)) if match else ""


def bullets(text: str, heading: str) -> list[str]:
    return [clean(m) for m in re.findall(r"^\s*[-*]\s+(?!\()(.+)$", section(text, heading), re.MULTILINE) if clean(m)]


def location(state_text: str) -> str:
    return field(state_text, "Current Situation", "Location")


def hooks(state_text: str) -> list[str]:
    return bullets(state_text, "Next Hooks")


def in_combat(state_text: str) -> bool:
    combat = section(state_text, "Active Combat")
    if re.search(r"status:?\**:?\s*finished\b", combat, re.IGNORECASE):
        return False
    return re.sub(r"[^a-z]", "", combat.lower()) not in {"", "none", "nocombat", "noactivecombat", "noneactive"}


_NAME = re.compile(r"^\s*\[[^\]\n]{1,60}\]:\s*")
_SUM = r"\d{1,3}(?:\s*(?:[+\-,/&=]|plus|minus|and)\s*\d{1,3})*"
_DICE = re.compile(
    rf"^\W*(?:{_SUM}(?:\s*total)?"
    rf"|(?:(?:i|we|he|she|they)\s+)?(?:(?:rolled|rolls|roll|got)\s+(?:an?\s+)?|nat(?:ural)?\s*|d\d+\s*[:=]?\s*)"
    rf"(?:nat(?:ural)?\s*)?{_SUM}(?:\s*(?:total|on\s+(?:the|my)\s+[\w' ]{{1,30}}|for\s+[\w' ]{{1,30}}))?)\W*$",
    re.IGNORECASE)
_SERVER_DICE = re.compile(r"\brolls\s+\d+d\d+.*=\s*-?\d+", re.IGNORECASE)
_OOC = re.compile(r"^\W*(?:ooc\b|out[ -]of[ -]character\b)|^\s*(?:\(\(|//)", re.IGNORECASE)


def counts_as_story(party_input: str) -> bool:
    """False when every line is only a dice result or is marked out of character."""
    lines = [_NAME.sub("", line).strip() for line in str(party_input or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return True
    return any(not (_DICE.match(line) or _SERVER_DICE.search(line) or _OOC.match(line)) for line in lines)


def turn_limit(state_text: str) -> int:
    flags = section(state_text, "Session Flags")
    explicit = re.search(r"^\s*(?:[-*]\s*)?steer_after:\s*(\d+)\s*$", flags, re.MULTILINE)
    if explicit:
        return int(explicit.group(1))
    spotlight = re.search(r"^\s*(?:[-*]\s*)?spotlight:\s*(\w+)", flags, re.MULTILINE)
    return SPOTLIGHT_LIMITS.get(spotlight.group(1) if spotlight else "balanced", SPOTLIGHT_LIMITS["balanced"])


@dataclass(frozen=True)
class _Target:
    key: str
    version: str
    first: Callable[[int], str]
    again: Callable[[int], str]


def _version(*parts: object) -> str:
    return hashlib.sha1("\x1f".join(clean(p) for p in parts).encode("utf-8")).hexdigest()[:12]


def _target(state_text: str, campaign: str) -> _Target | None:
    data = arc.read(state_text)
    where = location(state_text)
    at = f" at {where}" if where else ""
    steering = clean((data or {}).get("steering_notes"))
    hook = hooks(state_text)[:1]
    hook_text = f" A prepared hook can carry it: {hook[0].rstrip('.')}." if hook else ""
    advance = f"python3 {ARC_SCRIPT} advance --campaign {campaign}"
    beat = arc.current(data)
    if beat:
        bid, label = clean(beat["id"]), clean(beat.get("label"))
        pressure, change = clean(beat.get("world_pressure")), clean(beat.get("what_changes"))
        plan = (steering or f"{pressure}. Aim it at what this beat must change: {change}").rstrip(".")
        first = lambda turns: (f"the story has sat on beat {bid} \"{label}\" for {turns} party turns. This turn, resolve the "
                 f"party's action briefly, then make this happen now{at}, as something that demands a response: "
                 f"{plan}.{hook_text} Make it concrete: a person arrives, a deadline lands, a problem breaks open. "
                 "A hint or rumour is not enough.")
        again = lambda turns: (f"beat {bid} \"{label}\" has had a nudge already and the party is still on it ({turns} turns). "
                 f"Has this beat already landed? If so run {advance} now; if not, revise its pressure to fit where "
                 f"the party is{at}: python3 {ARC_SCRIPT} revise --campaign {campaign} --beat {bid} "
                 "--pressure \"...\" --steering \"...\", then play the new pressure this turn.")
        return _Target(bid, _version(pressure, change, steering), first, again)
    nxt = arc.structured_next(data)
    if nxt:
        name = f"chapter {nxt['chapter']}" + (f" \"{nxt['title']}\"" if nxt["title"] else "")
        guide = f" {steering}" if steering else ""
        first = lambda turns: (f"the party has spent {turns} turns in {name} without reaching its next key beat: "
                 f"\"{nxt['beat']}\". Read {nxt['source']} for how that beat plays, then bring it in this turn "
                 f"through what is already in front of the party{at}.{guide}")
        again = lambda turns: (f"the key beat \"{nxt['beat']}\" of {name} has had a nudge already ({turns} turns). Has it already "
                 f"happened? If so run {advance} now; if not, read {nxt['source']} and bring it in through the "
                 f"people and places in front of the party{at}.")
        return _Target(f"{nxt['chapter']}:{nxt['beat']}", _version(steering), first, again)
    return None


def directive(text: str) -> str:
    return ("DM steering, mandatory this turn: " + text +
            " Offer the party real choices and never decide for them. Do not mention the beat or the arc.")


def _read_pulse(campaign_dir: Path) -> dict:
    try:
        data = json.loads((campaign_dir / PULSE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def plan(campaign_dir: Path, party_input: str = "") -> Pulse | None:
    """The pulse after one more party turn, without saving it. None when this turn does not count."""
    try:
        state_text = (campaign_dir / "state.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if in_combat(state_text) or not counts_as_story(party_input):
        return None
    target = _target(state_text, campaign_dir.name)
    if target is None:
        return None
    saved = _read_pulse(campaign_dir)
    same = saved.get("beat") == target.key and saved.get("version", target.version) == target.version
    turns = (saved.get("turns", 0) if same else 0) + 1
    nudges = saved.get("nudges", 0) if same else 0
    limit = turn_limit(state_text)
    if limit and turns >= limit:
        return Pulse(target.key, target.version, 0, nudges + 1,
                     directive((target.again if nudges else target.first)(turns)))
    return Pulse(target.key, target.version, turns, nudges, None)


def commit(campaign_dir: Path, pulse: Pulse) -> None:
    path = campaign_dir / PULSE_FILE
    tmp = path.with_name(PULSE_FILE + ".tmp")
    tmp.write_text(json.dumps({"beat": pulse.beat, "version": pulse.version, "turns": pulse.turns,
                               "nudges": pulse.nudges}), encoding="utf-8")
    os.replace(tmp, path)
