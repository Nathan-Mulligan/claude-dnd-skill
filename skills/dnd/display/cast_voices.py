"""Speaker voices for a campaign's cast.json.

cast.json maps a lower-case speaker name to a TTS voice id. Beside those
entries it keeps `_sources`, which records who chose each voice:

    "dm"    set by the DM with `send.py --npc --npc-voice`
    "auto"  picked by the display when an unknown NPC first spoke
    absent  picked by hand in the voice menu (and every older entry)

A hand pick always wins: neither the DM nor the fallback ever replaces it.
"""
from __future__ import annotations

import hashlib
import json
import os
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
