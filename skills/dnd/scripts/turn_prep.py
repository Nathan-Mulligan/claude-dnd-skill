#!/usr/bin/env python3
"""Prep the numbers for a trade, a fight or a journey the party has just announced.

The turn card runs this while it builds the card, so the DM reads the facts instead of looking them
up: list prices and purses for a trade, stat blocks for named foes, the next road day for a journey.
It supplies numbers and rolls only; the story stays the DM's. At most one prep per turn, one line.

    python3 turn_prep.py <campaign-dir> "<party input>"   # prints the line, or nothing
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_FIGHT = re.compile(
    r"\b(attack(?:s|ed|ing)?|fight(?:s|ing)?|shoot(?:s|ing)?|stab(?:s|bed|bing)?|ambush(?:es|ed)?|"
    r"swing(?:s)? at|charge (?:at|the|in|toward|towards)|roll initiative|"
    r"draw (?:my|our|his|her|their) (?:blade|sword|bow|weapon|axe|dagger)s?)\b", re.I)
_TRADE = re.compile(
    r"\b(buy(?:s|ing)?|bought|sell(?:s|ing)?|sold|trade|trading|haggl\w*|barter\w*|purchas\w*|"
    r"price[sd]?|how much|costs?|wares)\b", re.I)
_ONWARD = r"(?:north|south|east|west|onward|onwards)"
_JOURNEY = re.compile(
    rf"\b(set(?:ting)? (?:out|off)|head(?:ing)? (?:out|off|{_ONWARD})|"
    rf"(?:travel|journey|ride|march)(?:l?ing)? (?:to|toward|towards|{_ONWARD})|"
    r"depart(?:s|ed|ing)?|hit the road|on the road to)\b", re.I)
MAX_FOES = 2
MAX_ITEMS = 4
MAX_LEN = 500
_NOT_FOES = {"tough", "shadow", "light", "duplicate"}
_NOT_ITEMS = {"pick", "case"}
# Everyday 2014 names the 2024 monster list renamed; used only where the old name has no record.
_FOE_ALIASES = {"Goblin Warrior": "goblin", "Hobgoblin Warrior": "hobgoblin", "Bugbear Warrior": "bugbear",
                "Kobold Warrior": "kobold", "Gnoll Warrior": "gnoll", "Sahuagin Warrior": "sahuagin",
                "Merfolk Skirmisher": "merfolk", "Centaur Trooper": "centaur", "Azer Sentinel": "azer",
                "Cultist Fanatic": "cult fanatic", "Warrior Veteran": "veteran", "Tough": "thug",
                "Priest Acolyte": "acolyte"}
_RULESET = re.compile(r"\*\*Ruleset:\*\*\s*(\d{4})", re.I)


def signal(party_input: str) -> str | None:
    """'fight', 'trade' or 'journey' when the party says so plainly, else None. Notes in [[...]] don't count."""
    said = re.sub(r"\[\[.*?\]\]", " ", party_input or "", flags=re.S)
    for kind, pattern in (("fight", _FIGHT), ("trade", _TRADE), ("journey", _JOURNEY)):
        if pattern.search(said):
            return kind
    return None


def _ruleset(campaign: Path) -> str:
    try:
        found = _RULESET.search((campaign / "state.md").read_text(encoding="utf-8"))
    except OSError:
        found = None
    return found.group(1) if found and found.group(1) in ("2014", "2024") else "2014"


def _plurals(name: str) -> set[str]:
    name = name.lower()
    forms = {name, name + "s", name + "es"}
    if name.endswith("f"):
        forms.add(name[:-1] + "ves")
    if name.endswith("y"):
        forms.add(name[:-1] + "ies")
    if name.endswith("s") and not name.endswith("ss"):
        forms.add(name[:-1])
    return forms


def _find(text: str, aliases: dict[str, list[dict]], limit: int) -> list[dict]:
    """Records named in `text`, longest names first, each stretch of text used once."""
    found, taken, low = [], [], text.lower()
    for alias in sorted(aliases, key=len, reverse=True):
        for match in re.finditer(rf"(?<![\w']){re.escape(alias)}(?![\w'])", low):
            if any(match.start() < end and start < match.end() for start, end in taken):
                continue
            taken.append(match.span())
            for record in aliases[alias]:
                if record not in found:
                    found.append(record)
            break
    return found[:limit]


def _alias_map(records: list[dict], names) -> dict[str, list[dict]]:
    aliases: dict[str, list[dict]] = {}
    for record in records:
        for name in names(record):
            for form in _plurals(name):
                if record not in aliases.setdefault(form, []):
                    aliases[form].append(record)
    return aliases


def _item_names(record: dict) -> list[str]:
    base = re.sub(r"\s*\(.*?\)", "", record.get("name", "")).strip()
    if "," not in base:
        return [base]
    head, tail = (part.strip() for part in base.split(",", 1))
    names = [base, f"{tail} {head}"]
    return names + ([head] if head.lower() not in _NOT_ITEMS else [])


def _foe_names(record: dict) -> list[str]:
    name = record.get("name", "")
    if "," in name or "(" in name:
        return []
    return [n for n in (name, _FOE_ALIASES.get(name)) if n and n.lower() not in _NOT_FOES]


def _records(lookup, category: str, ruleset: str) -> list[dict]:
    lookup._load_ruleset(ruleset)
    return lookup._data_by_rs.get(ruleset, {}).get(category, [])


def _trade(campaign: Path, said: str, lookup, inventory) -> str:
    items = [r for r in _records(lookup, "equipment", _ruleset(campaign)) if r.get("cost")]
    priced = [f"{r['name']} {r['cost']}" for r in _find(said, _alias_map(items, _item_names), MAX_ITEMS)]
    purses = []
    for sheet in inventory.character_sheets(campaign) if (campaign / "characters").is_dir() else []:
        try:
            ledger = inventory.load_ledger(sheet)
        except inventory.LedgerError:
            ledger = None
        if ledger:
            name = inventory.sheet_name(sheet.read_text(encoding="utf-8"), sheet.stem)
            purses.append(f"{name} {inventory.format_coins(ledger['coins'])}")
    parts = []
    if priced:
        parts.append("list prices (before the trade policy's margin): " + "; ".join(priced))
    if purses:
        parts.append("purses: " + "; ".join(purses))
    return "Prepared trade: " + ". ".join(parts) + "." if parts else ""


_ACTION = re.compile(r"^(?:Action|Bonus Action) — (.+)$", re.M)


def _attack(text: str) -> str:
    name, _, rest = text.partition(":")
    hit = re.search(r"([+-]\d+) to hit.*?Hit: (\d+ \([^)]*\) \w+)", rest)
    if hit:
        return f"{name.strip()} {hit.group(1)}, {hit.group(2)}"
    return re.sub(r"\s+", " ", name).strip()


def _cr(value) -> str:
    fractions = {0.125: "1/8", 0.25: "1/4", 0.5: "1/2"}
    if isinstance(value, float):
        return fractions.get(value) or f"{value:g}"
    return str(value if value is not None else "?")


def _fight(campaign: Path, said: str, lookup) -> str:
    monsters = [r for r in _records(lookup, "monsters", _ruleset(campaign))
                if r.get("hp") and str(r.get("type", "")).strip() not in ("", "custom")]
    aliases = _alias_map(monsters, _foe_names)
    for name in _FOE_ALIASES.values():
        named = [r for r in aliases.get(name, []) if r["name"].lower() == name]
        if named:
            for form in _plurals(name):
                aliases[form] = named
    blocks = []
    for r in _find(said, aliases, MAX_FOES):
        actions = [_attack(a) for a in _ACTION.findall(r.get("description", "")) if not a.startswith("Multiattack")]
        dex = (int(r.get("dex", 10)) - 10) // 2
        line = (f"{r['name']}: AC {r.get('ac', '?')}, HP {r['hp']} ({r.get('hp_dice', '')}), "
                f"speed {r.get('speed', '?')}, initiative {dex:+d}, CR {_cr(r.get('cr'))} ({r.get('xp', '?')} XP)")
        if actions:
            line += "; " + ", ".join(actions[:3])
        blocks.append(line)
    return "Prepared foes (SRD stat blocks, use them if these are the foes): " + " / ".join(blocks) + "." if blocks else ""


def _journey(campaign: Path, oracle) -> str:
    state = campaign / "state.md"
    if not state.is_file() or not oracle.is_improvised(state.read_text(encoding="utf-8")):
        return ""
    day = oracle.prepare_road(campaign)
    budget = f" {day['budget']}" if day.get("budget") else ""
    return (f"Prepared road, the next travel day, already rolled (calendar.py advance prints this same roll as "
            f"day 1; don't roll oracle.py road for it): {day['kind']}. {day['text']} {day['guidance']}{budget}")


def prepare(campaign: Path, party_input: str) -> str:
    kind = signal(party_input)
    if kind is None:
        return ""
    campaign = Path(campaign)
    said = re.sub(r"\[\[.*?\]\]", " ", party_input, flags=re.S)
    if kind == "fight":
        import lookup
        line = _fight(campaign, said, lookup)
    elif kind == "trade":
        import inventory
        import lookup
        line = _trade(campaign, said, lookup, inventory)
    else:
        import oracle
        line = _journey(campaign, oracle)
    line = re.sub(r"\s+", " ", line).replace("]]", "]").replace("|", "/").strip()
    return line if len(line) <= MAX_LEN else line[:MAX_LEN].rsplit(" ", 1)[0] + "..."


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    text = prepare(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "")
    if text:
        print(text)
