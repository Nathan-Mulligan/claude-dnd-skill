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

    if "feat" in low:
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
