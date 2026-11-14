"""Speaker voices for a campaign's cast.json.

cast.json maps a lower-case speaker name to a TTS voice id. Beside those
entries it keeps `_sources`, which records who chose each voice:

    "dm"    set by the DM with `send.py --npc --npc-voice`
    "auto"  picked by the display when an unknown NPC first spoke
    absent  picked by hand in the voice menu (and every older entry)

A hand pick always wins: neither the DM nor the fallback ever replaces it.

Older campaigns can hold clashing hand picks. A one-off repair re-casts only
the NPCs that share a voice with the narrator, a player character or an NPC
cast before them (shows the changes; --write saves them and keeps a backup):

    python3 cast_voices.py repair ~/.claude/dnd/campaigns/<Campaign> [--write]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Iterable

SOURCES = "_sources"
DM = "dm"
AUTO = "auto"


def cast_key(cast: dict, name: str) -> str:
    """Return the cast entry a speaker name resolves to.

    Mirrors `_castKey` in templates/index.html: an exact match, else the one
    entry whose first word is the name ("Nella" finds "nella reed").
    """
    raw = str(name or "").strip().lower()
    if raw in cast:
        return raw
    matches = [k for k in cast if not k.startswith("_") and k.split(" ")[0] == raw]
    return matches[0] if len(matches) == 1 else raw


def read_raw(path: Path) -> dict:
    """Read cast.json as stored, or {} when it is missing or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def voices(raw: dict) -> dict:
    """The name → voice entries, lower-cased, without bookkeeping keys."""
    return {str(k).lower(): v for k, v in raw.items()
            if isinstance(v, str) and not str(k).startswith("_")}


def sources(raw: dict) -> dict:
    src = raw.get(SOURCES)
    if not isinstance(src, dict):
        return {}
    return {str(k).lower(): v for k, v in src.items() if v in (DM, AUTO)}


def write_raw(path: Path, cast: dict, src: dict, comment=None) -> None:
    """Write cast.json atomically. Raises OSError on failure."""
    path = Path(path)
    data: dict = {}
    if isinstance(comment, str):
        data["_comment"] = comment
    data.update(cast)
    src = {k: v for k, v in src.items() if k in cast and v in (DM, AUTO)}
    if src:
        data[SOURCES] = src
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def pick_voice(name: str, pool: Iterable[str], *, narrator: str = "",
               party: Iterable[str] = (), recent: Iterable[str] = (),
               used: Iterable[str] = ()) -> str:
    """Pick a stable voice for a new speaker from `pool`.

    Tries, in order, to avoid: every voice already in the cast, the voices of
    recent speakers, the party's voices, and the narrator's. Drops the least
    important of those first when the pool runs out. Within the remaining
    choices the name's hash decides, so the same name and cast always give
    the same voice. Returns "" for an empty pool.
    """
    pool = list(dict.fromkeys(v for v in pool if v))
    if not pool:
        return ""
    narrator_set = {narrator} if narrator else set()
    party_set, recent_set, used_set = set(party), set(recent), set(used)
    tiers = (
        narrator_set | party_set | recent_set | used_set,
        narrator_set | party_set | recent_set,
        narrator_set | party_set,
        narrator_set,
        set(),
    )
    digest = int.from_bytes(
        hashlib.sha256(str(name).strip().lower().encode("utf-8")).digest()[:8], "big")
    for avoid in tiers:
        choices = [v for v in pool if v not in avoid]
        if choices:
            return choices[digest % len(choices)]
    return ""


def voice_gender(voice: str, male: Iterable[str] = (), female: Iterable[str] = ()) -> str:
    """'m', 'f' or '' for a voice id. A Kokoro id says it ("bf_emma"); a blend takes its first part."""
    first = str(voice or "").split("+")[0]
    if first in set(male):
        return "m"
    if first in set(female):
        return "f"
    prefix = first.split("_")[0]
    return prefix[1] if len(prefix) == 2 and prefix[1] in "mf" else ""


def blends(voices: Iterable[str]) -> list:
    """Two-voice Kokoro blends of the same gender, for when the plain voices run out."""
    voices = [v for v in voices if "_" in v and "+" not in v]
    return [f"{a}+{b}" for a in voices for b in voices
            if a < b and voice_gender(a) == voice_gender(b) != ""]


def repair(raw: dict, *, narrator: str, party: Iterable[str], pool: Iterable[str],
           male: Iterable[str] = (), female: Iterable[str] = ()) -> "tuple[dict, dict, list]":
    """Re-cast NPCs whose voice is shared, keeping everyone else.

    Player characters and the narrator keep their voices, and no NPC may share
    them. Of the NPCs sharing a voice, the one cast first (earliest in the file)
    keeps it. Each of the others gets an unused voice of the same gender, then
    an unused same-gender blend. Returns (cast, sources, changes), where each
    change is (name, old voice, new voice).
    """
    cast = voices(raw)
    src = sources(raw)
    pcs = {cast_key(cast, p) for p in party if p}
    taken = {narrator} if narrator else set()
    taken |= {cast[k] for k in pcs if k in cast}
    own = set(taken)          # nobody's blend may contain these either
    keep = set(pcs)
    for name, voice in cast.items():
        if name not in keep and voice not in taken:
            keep.add(name)
            taken.add(voice)
    pool = list(dict.fromkeys(v for v in pool if v))
    changes = []
    for name, voice in cast.items():
        if name in keep:
            continue
        gender = voice_gender(voice, male, female)
        same = [v for v in pool if voice_gender(v, male, female) == gender] or pool
        new = (pick_voice(name, [v for v in same if v not in taken])
               or pick_voice(name, [v for v in blends(v for v in same if v not in own) if v not in taken])
               or pick_voice(name, [v for v in blends(v for v in pool if v not in own) if v not in taken]))
        if not new:
            continue
        cast[name] = new
        src[name] = AUTO
        taken.add(new)
        changes.append((name, voice, new))
    return cast, src, changes


def _campaign_party(folder: Path) -> list:
    """Player character names: the sidebar's players, then the character sheets."""
    names = []
    try:
        stats = json.loads((folder / "party_stats.json").read_text(encoding="utf-8"))
        names += [str(p.get("name", "")) for p in stats.get("players", []) if isinstance(p, dict)]
    except (OSError, ValueError, AttributeError):
        pass
    sheets = folder / "characters"
    if sheets.is_dir():
        names += [p.stem.replace("-", " ") for p in sorted(sheets.glob("*.md"))]
    return [n.strip().lower() for n in names if n.strip()]


def _campaign_narrator(folder: Path, default: str) -> str:
    try:
        text = (folder / "state.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default
    m = re.search(r"^\s*tts_voice:\s*([A-Za-z0-9_+]+)\s*$", text, re.MULTILINE)
    return m.group(1) if m else default


def main(argv=None) -> int:
    import argparse
    import sys
    import time

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import tts

    ap = argparse.ArgumentParser(description="Give clashing NPC voices in a campaign's cast.json new, distinct ones.")
    ap.add_argument("command", choices=["repair"])
    ap.add_argument("campaign", help="the campaign folder, e.g. ~/.claude/dnd/campaigns/Enderal")
    ap.add_argument("--write", action="store_true", help="save the changes (a backup is kept); without it, only show them")
    args = ap.parse_args(argv)

    folder = Path(args.campaign).expanduser()
    path = folder / "cast.json"
    raw = read_raw(path)
    if not raw:
        print(f"No cast.json with voices in {folder}")
        return 1
    narrator = _campaign_narrator(folder, tts.LOCAL_DEFAULT_VOICE)
    if narrator in tts.GEMINI_VALID_VOICES:
        male, female = tts.GEMINI_VOICES_MALE, tts.GEMINI_VOICES_FEMALE
    else:
        male, female = tts.LOCAL_VOICES_MALE, tts.LOCAL_VOICES_FEMALE
    cast, src, changes = repair(raw, narrator=narrator, party=_campaign_party(folder),
                                pool=list(male) + list(female), male=male, female=female)
    if not changes:
        print("No clashing voices. Nothing to change.")
        return 0
    for name, old, new in changes:
        print(f"{name}: {old} -> {new}")
    if not args.write:
        print("Dry run. Add --write to save.")
        return 0
    backup = path.with_name(f"cast.json.before-repair-{time.strftime('%Y%m%d-%H%M%S')}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    write_raw(path, cast, src, raw.get("_comment"))
    print(f"Saved. The old file is {backup.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
