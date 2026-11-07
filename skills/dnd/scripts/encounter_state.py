#!/usr/bin/env python3
"""Persist basic weapon combat using combat.py's dice and attack rules.

The model chooses a target and narrates the result. This tool owns initiative,
turn order, attack specifications, damage and HP. Complex actions still use the
existing D&D procedures and are not silently treated as weapon attacks.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from combat import initiative_order, resolve_attack
from paths import campaign_dir


NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
DAMAGE_RE = re.compile(r"([1-9][0-9]*)?d([1-9][0-9]*)([+-][0-9]+)?\Z")
HP_RE = re.compile(r"(\*\*HP:\*\*\s*)([0-9]+)(\s*/\s*)([0-9]+)")


def _campaign_path(name: str) -> Path:
    if not NAME_RE.fullmatch(name) or name in {".", ".."}:
        raise ValueError("invalid campaign name")
    path = campaign_dir(name)
    if not (path / "state.md").is_file():
        raise ValueError(f"campaign state not found: {path / 'state.md'}")
    return path


def _state_file(name: str) -> Path:
    return _campaign_path(name) / "encounter.json"


def _atomic_json(path: Path, data: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".encounter-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_text(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".encounter-text-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load(name: str) -> dict:
    path = _state_file(name)
    if not path.is_file():
        raise ValueError("encounter not started")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (data.get("version") != 1 or data.get("campaign") != name
            or not isinstance(data.get("combatants"), list)
            or not isinstance(data.get("events"), list)
            or data.get("status") not in {"active", "paused", "finished"}
            or not isinstance(data.get("round"), int) or data["round"] < 1
            or not isinstance(data.get("cursor"), int)
            or not 0 <= data["cursor"] < len(data["combatants"])):
        raise ValueError("invalid encounter state")
    slot = data.setdefault("attack_slot", 1)
    if (not isinstance(slot, int) or isinstance(slot, bool) or slot < 1
            or slot > len(_profiles(data["combatants"][data["cursor"]]))):
        raise ValueError("invalid encounter attack slot")
    if not data.get("encounter_id"):
        data["encounter_id"] = uuid.uuid4().hex[:12]
        _atomic_json(path, data)
    return data


def _validate_combatants(combatants: list) -> list[dict]:
    if not isinstance(combatants, list) or len(combatants) < 2:
        raise ValueError("provide at least two combatants")
    result = []
    names = set()
    for source in combatants:
        if not isinstance(source, dict):
            raise ValueError("each combatant must be an object")
        name = source.get("name")
        if not isinstance(name, str) or not name.strip() or name.casefold() in names:
            raise ValueError("combatant names must be nonempty and unique")
        if name in {".", ".."} or any(char in name for char in ("/", "\\", "\0")):
            raise ValueError(f"unsafe combatant name: {name}")
        names.add(name.casefold())
        kind = source.get("type")
        if kind not in {"pc", "npc"}:
            raise ValueError(f"{name}: type must be pc or npc")
        values = {}
        for field in ("dex_mod", "hp", "ac"):
            value = source.get(field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name}: {field} must be an integer")
            values[field] = value
        if values["hp"] <= 0 or values["ac"] <= 0:
            raise ValueError(f"{name}: HP and AC must be positive")
        if "attacks" in source:
            if "attack_bonus" in source or "damage" in source:
                raise ValueError(f"{name}: cannot mix attacks with attack_bonus or damage")
            raw_profiles = source["attacks"]
            if not isinstance(raw_profiles, list) or not 1 <= len(raw_profiles) <= 4:
                raise ValueError(f"{name}: attacks must contain 1 to 4 fixed profiles")
        else:
            raw_profiles = [{"name": "Attack", "attack_bonus": source.get("attack_bonus"),
                             "damage": source.get("damage")}]
        profiles = []
        for profile in raw_profiles:
            if not isinstance(profile, dict):
                raise ValueError(f"{name}: each attack profile must be an object")
            label = profile.get("name")
            if (not isinstance(label, str) or not label.strip() or len(label) > 80
                    or any(char in label for char in ("\n", "\r", "|"))):
                raise ValueError(f"{name}: attack name must be a short plain label")
            bonus = profile.get("attack_bonus")
            if isinstance(bonus, bool) or not isinstance(bonus, int):
                raise ValueError(f"{name}: attack_bonus must be an integer")
            damage = profile.get("damage")
            match = DAMAGE_RE.fullmatch(damage) if isinstance(damage, str) else None
            if not match:
                raise ValueError(f"{name}: damage must be one dice expression such as 1d8+3")
            if int(match.group(1) or 1) > 100 or int(match.group(2)) > 1000:
                raise ValueError(f"{name}: damage dice size exceeds the encounter limit")
            profiles.append({"name": label, "attack_bonus": bonus, "damage": damage})
        result.append({"name": name, "type": kind, **values,
                       "max_hp": values["hp"], "attacks": profiles})
    if {c["type"] for c in result} != {"pc", "npc"}:
        raise ValueError("encounter needs both PCs and NPCs")
    return result


def _profiles(actor: dict) -> list[dict]:
    """Legacy saved encounters have one attack without an attacks array."""
    if not isinstance(actor, dict):
        raise ValueError("invalid encounter combatant")
    if "attacks" in actor:
        profiles = actor["attacks"]
        if not isinstance(profiles, list) or not 1 <= len(profiles) <= 4:
            raise ValueError("invalid saved attack profiles")
        return profiles
    if "attack_bonus" not in actor or "damage" not in actor:
        raise ValueError("invalid legacy attack profile")
    return [{"name": "Attack", "attack_bonus": actor["attack_bonus"],
             "damage": actor["damage"]}]


def _token(state: dict) -> str | None:
    if state["status"] != "active":
        return None
    actor = state["combatants"][state["cursor"]]
    base = f"{state['round']}:{state['cursor']}:{actor['name']}"
    return f"{base}:{state['attack_slot']}" if len(_profiles(actor)) > 1 else base


def _view(state: dict) -> dict:
    return {"campaign": state["campaign"], "status": state["status"],
            "round": state["round"], "turn_token": _token(state),
            "current": (state["combatants"][state["cursor"]]["name"]
                        if state["status"] == "active" else None),
            "attack_slot": state["attack_slot"],
            "combatants": state["combatants"], "events": state["events"]}


def start(name: str, combatants: list, max_rounds: int | None = None) -> dict:
    path = _state_file(name)
    if path.exists():
        raise ValueError("encounter already exists; finish or archive it before starting another")
    if not (_campaign_path(name) / "session-log.md").is_file():
        raise ValueError("campaign session-log.md not found")
    if max_rounds is not None and max_rounds < 1:
        raise ValueError("max rounds must be positive")
    validated = _validate_combatants(combatants)
    for actor in validated:
        if actor["type"] == "pc":
            _validate_sheet(_campaign_path(name) / "characters" / f"{actor['name']}.md",
                            actor["max_hp"], actor["hp"])
    ordered = initiative_order(validated)
    state = {"version": 1, "encounter_id": uuid.uuid4().hex[:12],
             "campaign": name, "status": "active", "round": 1,
             "cursor": 0, "attack_slot": 1, "max_rounds": max_rounds,
             "combatants": ordered, "events": []}
    _atomic_json(path, state)
    _sync_active_combat(name, state)
    return _view(state)


def _find(state: dict, name: str) -> dict:
    for combatant in state["combatants"]:
        if combatant["name"].casefold() == name.casefold():
            return combatant
    raise ValueError(f"unknown combatant: {name}")


def _advance(state: dict, continue_attacks: bool = False) -> None:
    alive_types = {c["type"] for c in state["combatants"] if c["hp"] > 0}
    if len(alive_types) < 2:
        state["status"] = "finished"
        return
    actor = state["combatants"][state["cursor"]]
    if continue_attacks and state["attack_slot"] < len(_profiles(actor)):
        state["attack_slot"] += 1
        return
    state["attack_slot"] = 1
    count = len(state["combatants"])
    for step in range(1, count + 1):
        index = (state["cursor"] + step) % count
        if state["combatants"][index]["hp"] <= 0:
            continue
        next_round = state["round"] + (1 if index <= state["cursor"] else 0)
        state["cursor"] = index
        state["round"] = next_round
        if state["max_rounds"] is not None and next_round > state["max_rounds"]:
            state["status"] = "paused"
        return
    raise ValueError("no living combatant remains")


def _validate_sheet(path: Path, maximum: int, current: int | None = None) -> str:
    if not path.is_file():
        raise ValueError(f"character sheet missing: {path}")
    original = path.read_text(encoding="utf-8")
    matches = list(HP_RE.finditer(original))
    if len(matches) != 1 or int(matches[0].group(4)) != maximum:
        raise ValueError(f"character sheet HP is ambiguous or max HP differs: {path}")
    if current is not None and int(matches[0].group(2)) != current:
        raise ValueError(f"character sheet current HP differs from encounter: {path}")
    return original


def _sheet_update(path: Path, current: int, maximum: int) -> None:
    original = _validate_sheet(path, maximum)
    updated = HP_RE.sub(lambda m: f"{m.group(1)}{current}{m.group(3)}{maximum}", original, count=1)
    fd, temporary = tempfile.mkstemp(prefix=".sheet-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_party(name: str) -> dict:
    state = _load(name)
    directory = _campaign_path(name) / "characters"
    synced = {}
    for actor in state["combatants"]:
        if actor["type"] != "pc":
            continue
        sheet = directory / f"{actor['name']}.md"
        _sheet_update(sheet, actor["hp"], actor["max_hp"])
        synced[actor["name"]] = actor["hp"]
    return synced


def _sync_active_combat(name: str, state: dict) -> None:
    path = _campaign_path(name) / "state.md"
    content = path.read_text(encoding="utf-8")
    # The compact party line is read at campaign load. Keep its HP in step with
    # the sheets so a fresh DM process does not inherit a stale summary.
    heading_start = re.search(r"(?m)^## ", content)
    preamble_end = heading_start.start() if heading_start else len(content)
    preamble, remainder = content[:preamble_end], content[preamble_end:]
    lines = preamble.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not re.match(r"^- \*\*Party:\*\*", line):
            continue
        for actor in state["combatants"]:
            if actor["type"] != "pc":
                continue
            pattern = re.compile(
                rf"(?P<prefix>(?<!\w){re.escape(actor['name'])}\s*\([^)]*?\bHP\s*)"
                rf"\d+(?P<slash>\s*/\s*)(?P<maximum>\d+)"
            )
            line = pattern.sub(
                lambda match: (
                    f"{match.group('prefix')}{actor['hp']}"
                    f"{match.group('slash')}{match.group('maximum')}"
                    if int(match.group("maximum")) == actor["max_hp"]
                    else match.group(0)
                ),
                line,
                count=1,
            )
        lines[index] = line
        break
    content = "".join(lines) + remainder
    status = state["status"]
    if status == "paused":
        label = f"Paused before round {state['round']}"
    elif status == "finished":
        label = f"Finished in round {state['round']}"
    else:
        label = f"Active in round {state['round']}"
    lines = [f"**Status:** {label}",
             f"**Current turn:** {_token(state) or 'none'}", "",
             "| Combatant | Side | HP | AC |",
             "|---|---|---:|---:|"]
    for actor in state["combatants"]:
        lines.append(f"| {actor['name']} | {actor['type']} | "
                     f"{actor['hp']}/{actor['max_hp']} | {actor['ac']} |")
    if status == "active":
        actor = state["combatants"][state["cursor"]]
        profiles = _profiles(actor)
        slot = state["attack_slot"]
        lines.extend(["", f"**Current attack:** {profiles[slot - 1]['name']} "
                      f"({slot}/{len(profiles)})"])
    section = "## Active Combat\n" + "\n".join(lines) + "\n"
    heading = re.search(r"(?m)^## Active Combat[ \t]*$", content)
    if heading:
        next_heading = re.search(r"(?m)^## [^\n]+$", content[heading.end():])
        end = heading.end() + next_heading.start() if next_heading else len(content)
        updated = content[:heading.start()] + section + "\n" + content[end:]
    else:
        updated = content.rstrip() + "\n\n" + section
    _atomic_text(path, updated)


def _round_summary(state: dict, round_number: int) -> str:
    actions = []
    for event in state["events"]:
        if event["round"] != round_number:
            continue
        if event["kind"] == "pass":
            actions.append(f"{event['actor']} passed")
            continue
        attack = event["attack"]
        outcome = "hit" if attack["hit"] else "missed"
        damage = attack.get("damage", 0)
        label = f" ({event['attack_name']})" if "attack_name" in event else ""
        actions.append(
            f"{event['actor']}{label} {outcome} {event['target']} "
            f"(d20 {attack['d20']}+{attack['attack_bonus']}={attack['total']} "
            f"vs AC {attack['target_ac']}; {damage} damage; "
            f"HP {event['hp_before']}→{event['hp_after']})"
        )
    return f"Round {round_number}: " + "; ".join(actions) + "."


def _append_recent(state_path: Path, marker: str, summary: str) -> bool:
    content = state_path.read_text(encoding="utf-8")
    if marker in content:
        return False
    entry = f"- [{marker}] {summary}\n"
    heading = "## Recent Events"
    if heading not in content:
        updated = content.rstrip() + f"\n\n{heading}\n{entry}"
    else:
        before, after = content.split(heading, 1)
        updated = before + heading + "\n" + entry + after.lstrip("\n")
    _atomic_text(state_path, updated)
    return True


def checkpoint(name: str) -> list[str]:
    state = _load(name)
    completed = state["round"] - 1
    if state["status"] == "finished":
        completed = state["round"]
    directory = _campaign_path(name)
    state_path = directory / "state.md"
    log_path = directory / "session-log.md"
    written = []
    for round_number in range(1, completed + 1):
        if not any(event["round"] == round_number for event in state["events"]):
            continue
        marker = f"encounter:{state['encounter_id']}:round:{round_number}"
        summary = _round_summary(state, round_number)
        _append_recent(state_path, marker, summary)
        log = log_path.read_text(encoding="utf-8")
        if marker not in log:
            _atomic_text(log_path, log.rstrip() + f"\n- [{marker}] {summary}\n")
            written.append(marker)
    return written


def _act(name: str, token: str, kind: str, target_name: str | None = None,
         allow_friendly_fire: bool = False) -> dict:
    state = _load(name)
    for previous in state["events"]:
        if previous["token"] == token:
            if previous["kind"] != kind or previous.get("target") != target_name:
                raise ValueError("turn token was already used for a different action")
            sync_party(name)
            _sync_active_combat(name, state)
            checkpoint(name)
            return {"replayed": True, "event": previous, "next": _view(state)}
    if token != _token(state):
        raise ValueError(f"wrong turn token; current token is {_token(state)}")
    actor = state["combatants"][state["cursor"]]
    event = {"token": token, "kind": kind, "round": state["round"],
             "actor": actor["name"]}
    if kind == "attack":
        if target_name is None:
            raise ValueError("attack needs a target")
        target = _find(state, target_name)
        if target["hp"] <= 0 or target is actor:
            raise ValueError("target must be living and distinct from actor")
        if target["type"] == actor["type"] and not allow_friendly_fire:
            raise ValueError("same-side target needs --allow-friendly-fire")
        if target["type"] == "pc":
            sheet = _campaign_path(name) / "characters" / f"{target['name']}.md"
            _validate_sheet(sheet, target["max_hp"], target["hp"])
        profile = _profiles(actor)[state["attack_slot"] - 1]
        result = resolve_attack(profile["attack_bonus"], target["ac"], profile["damage"])
        before = target["hp"]
        target["hp"] = max(0, before - max(0, result.get("damage", 0)))
        event.update({"target": target["name"], "attack_name": profile["name"],
                      "attack_slot": state["attack_slot"], "attack": result,
                      "hp_before": before, "hp_after": target["hp"]})
    elif kind != "pass":
        raise ValueError(f"unsupported action: {kind}")
    state["events"].append(event)
    _advance(state, continue_attacks=(kind == "attack"))
    _atomic_json(_state_file(name), state)
    sync_party(name)
    _sync_active_combat(name, state)
    checkpoint(name)
    return {"replayed": False, "event": event, "next": _view(state)}


def resume(name: str, max_rounds: int) -> dict:
    state = _load(name)
    if state["status"] != "paused" or max_rounds < state["round"]:
        raise ValueError("resume requires a paused encounter and a sufficient round limit")
    state["max_rounds"] = max_rounds
    state["status"] = "active"
    _atomic_json(_state_file(name), state)
    _sync_active_combat(name, state)
    return _view(state)


def archive(name: str) -> Path:
    state = _load(name)
    if state["status"] != "finished":
        raise ValueError("only a finished encounter can be archived")
    sync_party(name)
    _sync_active_combat(name, state)
    checkpoint(name)
    directory = _campaign_path(name) / "encounters"
    directory.mkdir(exist_ok=True)
    number = 1
    while (directory / f"encounter-{number:04d}.json").exists():
        number += 1
    destination = directory / f"encounter-{number:04d}.json"
    os.rename(_state_file(name), destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("start", "status", "attack", "pass", "resume", "sync", "checkpoint", "archive"):
        item = sub.add_parser(command)
        item.add_argument("--campaign", required=True)
        if command == "start":
            item.add_argument("--combatants", required=True, help="JSON array with fixed attack specs")
            item.add_argument("--max-rounds", type=int)
        if command in {"attack", "pass"}:
            item.add_argument("--token", required=True, help="turn_token from status or previous result")
        if command == "attack":
            item.add_argument("--target", required=True)
            item.add_argument("--allow-friendly-fire", action="store_true")
        if command == "resume":
            item.add_argument("--max-rounds", required=True, type=int)
    args = parser.parse_args()
    try:
        if args.command == "start":
            result = start(args.campaign, json.loads(args.combatants), args.max_rounds)
        elif args.command == "status":
            result = _view(_load(args.campaign))
        elif args.command == "attack":
            result = _act(args.campaign, args.token, "attack", args.target,
                          args.allow_friendly_fire)
        elif args.command == "pass":
            result = _act(args.campaign, args.token, "pass")
        elif args.command == "resume":
            result = resume(args.campaign, args.max_rounds)
        elif args.command == "archive":
            result = {"archived_to": str(archive(args.campaign))}
        elif args.command == "checkpoint":
            result = {"written": checkpoint(args.campaign)}
        else:
            result = sync_party(args.campaign)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.exit(2, f"encounter_state.py: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
