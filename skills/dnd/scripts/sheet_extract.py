#!/usr/bin/env python3
"""Read a character sheet's features and inventory into the display's `sheet` keys.

The DM used to rebuild `sheet.features` and `sheet.inventory` by hand at every
load, and split feature names out of prose by guesswork ("Medium; speed 30 ft;
Common and two additional languages" became a feature name). This module does
it the same way every time:

  * features come from `## Features & Traits`, grouped by the `###` heading
    above them (class, species, background or feat), one entry per named
    feature, with the full text kept for the click-to-open view;
  * inventory comes from the character's ledger when there is one
    (see inventory.py), otherwise from the section's prose lines.

    python3 sheet_extract.py --campaign Enderal [--character elbane-iii]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import inventory  # noqa: E402

FEATURES_HEADING = re.compile(r"(?m)^##[ \t]+Features(?:[ \t]*(?:&|and)[ \t]*(?:Traits|Passives))?[ \t]*$")
_BOLD = re.compile(r"^\*\*(.+?)[.:]?\*\*[.:]?\s*(.*)$", re.S)
_NAMED = re.compile(r"^([A-Z][\w'’()/-]*(?:[ \t]+[\w'’()/-]+){0,4}):\s*(.*)$", re.S)
_SIZE = re.compile(r"^(?:Tiny|Small|Medium|Large)\b.*?\bspeed\b|^Speed\s+\d+", re.I | re.S)
# A "Skills:" or "Saving throws:" clause belongs to the proficiencies before it.
_PROFICIENCY_PART = re.compile(r"(?:class |bonus )?(?:skills?|saving throws?|saves|tools?|languages?|"
                               r"armou?r|weapons?|instruments?)", re.I)
_TAG = re.compile(r"\s+\(?(origin feat|general feat|fighting style feat|epic boon|feat)\)?$", re.I)
_KIND_LABEL = {"class": "Class", "species": "Species", "background": "Background", "feat": "Feats", "other": ""}


def _identity(text: str) -> dict:
    def field(*names: str) -> str:
        for name in names:
            m = re.search(rf"\*\*{name}:\*\*\s*([^|\n]+)", text, re.I)
            if m and m.group(1).strip():
                return m.group(1).strip()
        return ""
    return {
        "class": field("Class"),
        "species": field("Species", "Race"),
        "background": field("Background"),
    }


def _source(heading: str) -> str:
    heading = heading.strip().strip("<>").strip()
    return re.sub(r"\s+(Racial|Species|Background|Features?|Traits?|Class)(\s+Traits?)?$", "", heading, flags=re.I) or heading


def _kind(heading: str, source: str, who: dict) -> str:
    low, src = heading.lower(), source.lower()

    def same(value: str) -> bool:
        words = re.findall(r"[a-z']+", value.lower())
        return bool(words) and src in {value.lower(), words[0]}

    if re.search(r"\bfeats?\b", low):
        return "feat"
    if same(who["class"]) or low.endswith(" class"):
        return "class"
    if same(who["species"]) or "racial" in low or "species" in low:
        return "species"
    if same(who["background"]) or "background" in low:
        return "background"
    return "other"


def _segments(text: str) -> list[tuple[int, str]]:
    """Split at '; ' and sentence ends, keeping each piece's start offset."""
    out, start = [], 0
    for m in re.finditer(r";\s+|(?<=[a-z0-9)])\.\s+(?=[A-Z])", text):
        out.append((start, text[start:m.start()]))
        start = m.end()
    out.append((start, text[start:]))
    return [(i, s) for i, s in out if s.strip()]


def _clean(text: str) -> str:
    text = text.strip().rstrip(";").strip()
    return text[:1].upper() + text[1:] if text else text


def _unnamed(text: str) -> str:
    if _SIZE.search(text):
        return "Size, speed and languages"
    if re.search(r"proficien", text, re.I):
        return "Proficiencies"
    return "Notes"


def split_entry(line: str) -> list[dict]:
    """One sheet line → one or more {name, text}."""
    text = re.sub(r"^\s*[-*]\s+", "", line).strip()
    if not text:
        return []
    m = _BOLD.match(text)
    if m:
        return [{"name": m.group(1).strip(), "text": _clean(m.group(2)) or m.group(1).strip()}]
    if _SIZE.search(text):
        return [{"name": _unnamed(text), "text": _clean(text)}]
    features: list[dict] = []
    segments = _segments(text)
    for offset, segment in segments:
        lead = len(segment) - len(segment.lstrip())
        seg = segment.strip()
        named = _NAMED.match(seg)
        if named and not re.search(r"\d", named.group(1)) and not (
                features and _PROFICIENCY_PART.fullmatch(named.group(1).strip())):
            body_at = offset + lead + named.start(2)
            features.append({"name": named.group(1).strip(), "seg": offset, "body": body_at})
        elif not features:
            short = len(seg.split()) <= 4 and seg[:1].isupper() and not re.search(r"\d|,", seg)
            if short and len(segments) > 1:
                features.append({"name": seg.rstrip("."), "seg": offset, "body": None})
            else:
                features.append({"name": _unnamed(text), "seg": offset, "body": offset})
    out = []
    for i, feature in enumerate(features):
        end = features[i + 1]["seg"] if i + 1 < len(features) else len(text)
        body = "" if feature["body"] is None else _clean(text[feature["body"]:end])
        out.append({"name": feature["name"], "text": body})
    return out


def _tidy_name(entry: dict) -> dict:
    m = _TAG.search(entry["name"])
    if m and entry["name"][:m.start()].strip():
        entry["name"] = entry["name"][:m.start()].strip()
        entry["tag"] = m.group(1)[:1].upper() + m.group(1)[1:].lower()
    return entry


def parse_features(text: str) -> list[dict]:
    """`## Features & Traits` → [{name, text, source, kind[, tag]}] in sheet order."""
    m = FEATURES_HEADING.search(text)
    if not m:
        return []
    nxt = inventory.NEXT_HEADING.search(text, m.end())
    section = text[m.end():nxt.start() if nxt else len(text)]
    who = _identity(text)
    source, kind = "", "other"
    out: list[dict] = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line or line.startswith("<!--"):
            continue
        h = re.match(r"^###\s+(.+)$", line)
        if h:
            source = _source(h.group(1))
            kind = _kind(h.group(1), source, who)
            continue
        if line in {"-", "*"}:
            continue
        for entry in split_entry(line):
            entry = _tidy_name(entry)
            entry.update({"source": source, "kind": kind})
            if kind != "other":
                entry["kind_label"] = _KIND_LABEL[kind]
            out.append(entry)
    return out


def prose_inventory(text: str) -> list[str]:
    bounds = inventory._section_bounds(text, inventory.INVENTORY_HEADING)
    if not bounds:
        return []
    body = text[bounds[0]:bounds[1]].split("\n", 1)
    lines = body[1].splitlines() if len(body) > 1 else []
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("<!--") and l.strip() != "-"]


def sheet_payload(campaign: Path, sheet: Path) -> tuple[str, dict]:
    """(character name, the display `sheet` keys built from the files)."""
    text = sheet.read_text(encoding="utf-8")
    name = inventory.sheet_name(text, sheet.stem)
    payload: dict = {"features": parse_features(text)}
    view = inventory.display_sheet(sheet, inventory.load_ledger(sheet), inventory.load_cargo(campaign))
    payload.update(view or {"inventory": prose_inventory(text)})
    return name, payload


_ABILITIES = ("str", "dex", "con", "int", "wis", "cha")


def _section(text: str, title: str) -> str:
    m = re.search(rf"(?m)^##[ \t]+{title}[^\n]*\n(.*?)(?=^##[ \t]|\Z)", text, re.S)
    return m.group(1) if m else ""


def _bold(text: str, label: str) -> str:
    m = re.search(rf"\*\*{label}:\*\*\s*([^|\n]*)", text, re.I)
    return m.group(1).strip() if m else ""


def _int(value: str) -> "int | None":
    m = re.search(r"-?\d+", value or "")
    return int(m.group()) if m else None


def _table(section: str) -> list[list[str]]:
    rows = [[c.strip() for c in line.strip().strip("|").split("|")]
            for line in section.splitlines() if line.strip().startswith("|")]
    return [r for r in rows[1:] if not all(set(c) <= set("-: ") for c in r)]


_SPELL_LABELS = (("prepared", r"Prepared(?: spells)?|Known spells|Spells known"), ("cantrips", r"Cantrips"))


def _spells(section: str) -> dict:
    """Spell lists by label, each read from its own line up to the end of its sentence."""
    spells: dict = {}
    for line in section.splitlines():
        text = re.sub(r"^\s*[-*]\s*", "", line).replace("**", "")
        for key, label in _SPELL_LABELS:
            m = re.search(rf"(?:^|\.\s+)(?:{label})\s*:\s*([^.]*)", text, re.I)
            if m and key not in spells:
                spells[key] = [s.strip() for s in m.group(1).split(",") if s.strip() and s.strip().lower() != "none"]
        if re.search(r"\bno cantrips\b", text, re.I):
            spells["cantrips"] = []
    flat = " ".join(section.split())
    dc = re.search(r"save DC\s*(\d+)", flat, re.I)
    attack = re.search(r"spell attack(?: bonus)?\s*([+-]\d+)", flat, re.I)
    if dc:
        spells["save_dc"] = int(dc.group(1))
    if attack:
        spells["attack_bonus"] = attack.group(1)
    return spells


def _inspiration(value: str) -> "bool | None":
    if re.match(r"\s*(?:yes|true|held|[✓✔])", value, re.I):
        return True
    if re.match(r"\s*(?:no|none|false|[—–-]\s*$)", value, re.I):
        return False
    number = _int(value)
    return None if number is None else number > 0


def player_payload(campaign: Path, sheet: Path) -> dict:
    """One display player built from the sheet file; fields the sheet leaves blank are left out."""
    text = sheet.read_text(encoding="utf-8")
    name, view = sheet_payload(campaign, sheet)
    player: dict = {"name": name}
    who = _identity(text)
    for key, value in (("race", who["species"]), ("class", who["class"])):
        if value:
            player[key] = value
    level = _int(_bold(text, "Level"))
    if level:
        player["level"] = level
    xp = re.search(r"\*\*XP:\*\*\s*(\d+)\s*/\s*(\d+)", text)
    if xp:
        player["xp"] = {"current": int(xp.group(1)), "next": int(xp.group(2))}
    combat = _section(text, "Combat Stats")
    hp = re.search(r"\*\*HP:\*\*\s*(\d+)\s*/\s*(\d+)", combat)
    if hp:
        player["hp"] = {"current": int(hp.group(1)), "max": int(hp.group(2)), "temp": _int(_bold(combat, "Temp HP")) or 0}
    ac, speed = _int(_bold(combat, "AC")), _int(_bold(combat, "Speed"))
    if ac is not None:
        player["ac"] = ac
    initiative = re.match(r"[+-]?\d+", _bold(combat, "Initiative"))
    if initiative:
        player["initiative"] = initiative.group() if initiative.group()[0] in "+-" else "+" + initiative.group()
    if speed is not None:
        player["speed"] = speed
    dice = re.search(r"\*\*Hit Dice:\*\*\s*(\d+)(d\d+)(?:\s*\(remaining:\s*(\d+)\))?", combat)
    if dice:
        player["hit_dice"] = {"max": int(dice.group(1)), "remaining": int(dice.group(3) or dice.group(1)),
                              "die": dice.group(2)}
    inspiration = _inspiration(_bold(combat, "Heroic Inspiration") or _bold(combat, "Inspiration"))
    if inspiration is not None:
        player["inspiration"] = inspiration
    rows = _table(_section(text, "Ability Scores"))
    scores = [re.match(r"\s*(\d+)", c) for c in rows[0]] if rows else []
    if len(scores) == 6 and all(scores):
        player["ability_scores"] = {}
        for key, m in zip(_ABILITIES, scores):
            mod = (int(m.group(1)) - 10) // 2
            player["ability_scores"][key] = {"score": int(m.group(1)), "mod": f"+{mod}" if mod >= 0 else str(mod)}
    slots = {}
    for row in _table(_section(text, "Spell Slots")):
        level_n, total = _int(row[0]), _int(row[1]) if len(row) > 1 else None
        if level_n and total:
            slots[str(level_n)] = {"used": _int(row[2]) or 0 if len(row) > 2 else 0, "max": total}
    if slots:
        player["spell_slots"] = slots
    sheet_view = dict(view)
    attacks = [dict(zip(("name", "bonus", "damage", "type", "notes"), row)) for row in _table(_section(text, "Attacks"))
               if row and row[0]]
    if attacks:
        sheet_view["attacks"] = attacks
    spells = _spells(_section(text, "Known Spells"))
    if spells:
        sheet_view["spells"] = spells
    player["sheet"] = sheet_view
    return player


def party_payloads(campaign_name: str) -> list[dict]:
    campaign = inventory.campaign_path(campaign_name)
    return [player_payload(campaign, sheet) for sheet in inventory.character_sheets(campaign)]


def campaign_payloads(campaign_name: str) -> list[tuple[str, dict]]:
    campaign = inventory.campaign_path(campaign_name)
    return [sheet_payload(campaign, sheet) for sheet in inventory.character_sheets(campaign)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--character")
    args = parser.parse_args(argv)
    try:
        campaign = inventory.campaign_path(args.campaign)
        sheets = ([inventory.resolve_sheet(campaign, args.character)] if args.character
                  else inventory.character_sheets(campaign))
        result = {name: data for name, data in (sheet_payload(campaign, s) for s in sheets)}
    except (inventory.LedgerError, OSError) as error:
        print(f"sheet_extract.py: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
