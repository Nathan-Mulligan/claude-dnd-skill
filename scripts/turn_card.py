#!/usr/bin/env python3
"""The DM's per-turn cheat sheet, built from the campaign files.

The portal puts `prepare(...)`'s card in front of every party reply. The card is one line, because
the portal types it into the DM's terminal, where a newline would submit the message early.

    python3 scripts/turn_card.py <campaign-dir> ["party input"]   # print the card for debugging
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import arc_pulse  # noqa: E402
from arc_pulse import bullets, clean, field, section  # noqa: E402

RULES = (
    "End on something in motion, never a bare \"what do you do?\"",
    "Before any roll, name the DC and the stake, and resolve the roll this turn",
    "Never decide for the player characters",
    "Turn shape: one send.py call with every block of narration first (a player roll splits it: stake with "
    "the roll request, result after), bookkeeping in the next call, then reply done",
)
ROLL_MODES = {"auto": "you roll openly", "players": "players roll (call for the roll and wait)"}
CLOCK_WINDOW = 2
MAX_NPCS = 3
PREP_SCRIPT = Path(__file__).resolve().parents[1] / "skills/dnd/scripts/turn_prep.py"
PREP_TIMEOUT = 5


def _clip(text: str, limit: int) -> str:
    text = clean(text).replace("]]", "]").replace("|", "/")
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _where(state: str) -> str:
    place = arc_pulse.location(state)
    when = field(state, "Current Situation", "In-world date/time") or field(state, "World State", "In-world date")
    joined = ", ".join(p for p in (place, when) if p)
    return f"Where: {_clip(joined, 200)}" if joined else ""


def _beat(state: str) -> str:
    data = arc_pulse.arc.read(state)
    steering = clean((data or {}).get("steering_notes"))
    beat = arc_pulse.arc.current(data)
    if beat:
        plan = steering or (f"pressure: {clean(beat.get('world_pressure'))}; it lands when: "
                            f"{clean(beat.get('what_changes'))}")
        return _clip(f"Beat {clean(beat['id'])} \"{clean(beat.get('label'))}\": {plan}", 520)
    nxt = arc_pulse.arc.structured_next(data)
    if nxt:
        title = f" \"{nxt['title']}\"" if nxt["title"] else ""
        guide = f" Steering: {steering}" if steering else ""
        return _clip(f"Chapter {nxt['chapter']}{title}: next key beat \"{nxt['beat']}\" (read {nxt['source']}).{guide}", 520)
    return ""


def _is_structured(state: str) -> bool:
    return (arc_pulse.arc.read(state) or {}).get("type") == "structured"


def _hooks(state: str) -> str:
    found = arc_pulse.hooks(state)[:2]
    if found:
        return "Next hooks: " + "; ".join(_clip(h, 180).rstrip(".") for h in found)
    if "## Campaign Arc" in state and not _is_structured(state):
        return "Next hooks: none written. Write two or three under ## Next Hooks in state.md before you narrate."
    return ""


_DATE = re.compile(r"(?:day\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([A-Za-z][A-Za-z'-]+),?(?:\s+(\d{1,6}))?", re.IGNORECASE)


def _calendar(campaign_dir: Path) -> dict:
    try:
        data = json.loads(_read(campaign_dir / "calendar.json") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _day_number(cal: dict, day: int, month: int, year: int) -> int:
    months = len(cal.get("months") or []) or 12
    return (year * months + month - 1) * int(cal.get("month_length") or 30) + day


def _parse_date(text: str, cal: dict, default_year: int) -> int | None:
    months = [str(m).lower() for m in cal.get("months") or []]
    for match in _DATE.finditer(text):
        name = match.group(2).lower()
        if name in months:
            year = int(match.group(3)) if match.group(3) else default_year
            return _day_number(cal, int(match.group(1)), months.index(name) + 1, year)
    return None


def _today(state: str, cal: dict) -> tuple[int, int] | None:
    try:
        day, month, year = int(cal["day"]), int(cal["month"]), int(cal["year"])
        return _day_number(cal, day, month, year), year
    except (KeyError, TypeError, ValueError):
        pass
    text = field(state, "Current Situation", "In-world date/time") or field(state, "World State", "In-world date")
    year = re.search(r"\b(\d{3,6})\b", text)
    if not year:
        return None
    parsed = _parse_date(text, cal, int(year.group(1)))
    return (parsed, int(year.group(1))) if parsed is not None else None


def _clocks(state: str, campaign_dir: Path) -> str:
    entries = bullets(state, "Clocks")
    cal = _calendar(campaign_dir)
    today = _today(state, cal) if entries else None
    if not today:
        return ""
    due = []
    for entry in entries:
        date_part = entry.split(":", 1)[0]
        when = _parse_date(date_part, cal, today[1])
        if when is None:
            continue
        gap = when - today[0]
        if gap <= CLOCK_WINDOW:
            tag = "overdue" if gap < 0 else "today" if gap == 0 else f"in {gap} day{'s' if gap > 1 else ''}"
            due.append((when, f"{_clip(entry, 200)} [{tag}]"))
    if not due:
        return ""
    return "Clocks due, show them in the fiction: " + "; ".join(text for _, text in sorted(due)[:2])


def _trade(state: str) -> str:
    match = re.search(r"^\s*[-*]\s+\**trade policy:?\**:?\s*(.+)$", section(state, "Pinned Facts"), re.MULTILINE | re.IGNORECASE)
    return f"Trade policy (apply it without asking): {_clip(match.group(1), 220)}" if match else ""


def _npc_entries(npcs: str) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    rows = [r for r in npcs.splitlines() if r.strip().startswith("|")]
    header = [c.strip().lower() for c in rows[0].strip().strip("|").split("|")] if rows else []
    for row in rows[1:]:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        name = clean(cells[0]) if cells else ""
        if name and not set(name) <= set("-: ") and name.lower() != "name":
            entries.setdefault(name, {}).update({k: clean(v) for k, v in zip(header, cells) if clean(v)})
    for match in re.finditer(r"^#{2,3} +(.+?)\s*$\n(.*?)(?=^#{1,3} |\Z)", npcs, re.MULTILINE | re.DOTALL):
        name, body = clean(match.group(1)), match.group(2)
        if name.lower() in {"personality", "relationships", "notes"} or "<" in name:
            continue
        entry = entries.setdefault(name, {})
        for label, key in (("Attitude toward party", "attitude"), ("Demeanor", "demeanor"), ("Temperament", "temperament"),
                           ("Wants now", "wants")):
            found = re.search(rf"\**{label}:?\**:?\s*([^|\n]+)", body, re.IGNORECASE)
            if found and clean(found.group(1)):
                entry.setdefault(key, clean(found.group(1)))
        first = re.split(r"(?<=[.;])\s", clean(body), maxsplit=1)[0]
        if first and not first.startswith("-"):
            entry.setdefault("summary", first)
    return entries


def _mentioned(name: str, text: str, first_names: dict[str, int]) -> bool:
    if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
        return True
    first = name.split()[0]
    return len(first) >= 4 and first_names.get(first.lower()) == 1 and bool(re.search(rf"\b{re.escape(first)}\b", text))


def _npcs(state: str, campaign_dir: Path, party_input: str) -> str:
    entries = _npc_entries(_read(campaign_dir / "npcs.md"))
    if not entries:
        return ""
    scene = " ".join((arc_pulse.location(state), field(state, "Current Situation", "Party status"), party_input))
    first_names: dict[str, int] = {}
    for name in entries:
        first_names[name.split()[0].lower()] = first_names.get(name.split()[0].lower(), 0) + 1
    present = []
    for name, info in entries.items():
        if not _mentioned(name, scene, first_names):
            continue
        attitude = info.get("attitude", "")
        nature = info.get("temperament") or info.get("demeanor") or info.get("role") or info.get("summary", "")
        wants = f"wants {info['wants']}" if info.get("wants") else ""
        detail = "; ".join(_clip(p, 90).rstrip(".;") for p in (nature, attitude, wants) if p)
        present.append(f"{name} ({detail})" if detail else name)
        if len(present) == MAX_NPCS:
            break
    return "People here: " + "; ".join(present) if present else ""


def _load_prep():
    # Loaded by path for its stdlib-only signal(); putting skills/dnd/scripts on sys.path would let its
    # calendar.py shadow the stdlib. The prep itself runs in its own process for the same reason.
    name = "dnd_turn_prep"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PREP_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _prepared(campaign_dir: Path, party_input: str) -> str:
    if not _load_prep().signal(party_input):
        return ""
    try:
        done = subprocess.run([sys.executable, str(PREP_SCRIPT), str(campaign_dir), party_input],
                              capture_output=True, text=True, encoding="utf-8", timeout=PREP_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _table(state: str, runtime_dir: Path | None) -> str:
    parts = []
    target = _read(runtime_dir / "narration_target").strip() if runtime_dir else ""
    if target.isdigit() and int(target) > 0:
        parts.append(f"narration about {target} words, no padding")
    mode = re.search(r"^\s*(?:[-*]\s*)?roll_mode:\s*(\w+)", section(state, "Session Flags"), re.MULTILINE)
    rolls = []
    if mode and mode.group(1) in ROLL_MODES:
        rolls.append(ROLL_MODES[mode.group(1)])
    if rolls:
        parts.append("rolls: " + ", ".join(rolls))
    return "Table: " + "; ".join(parts) if parts else ""


def _roll_overrides(runtime_dir: Path | None) -> str:
    """Per-character overrides from the display's Rolls toggle, in check_input.py's format."""
    try:
        prefs = json.loads(_read(runtime_dir / "roll_prefs.json") or "{}") if runtime_dir else {}
    except ValueError:
        return ""
    if not isinstance(prefs, dict):
        return ""
    return "".join(f"[[{_clip(who, 60)} roll mode: {how}]] " for who, how in prefs.items() if how in ROLL_MODES)


def _lines(campaign_dir: Path, party_input: str, runtime_dir: Path | None, pulse) -> list[str]:
    state = _read(campaign_dir / "state.md")
    if not state:
        return []
    steering = _clip(pulse.directive, 1200) if pulse is not None and pulse.directive else ""
    makers = (
        lambda: _where(state),
        lambda: steering or _beat(state),
        lambda: _hooks(state),
        lambda: _clocks(state, campaign_dir),
        lambda: _trade(state),
        lambda: _npcs(state, campaign_dir, party_input),
        lambda: _prepared(campaign_dir, party_input),
        lambda: _table(state, runtime_dir),
    )
    lines = []
    for make in makers:
        try:
            line = make()
        except Exception:  # one bad section must not cost the DM the rest of the card
            line = ""
        if line:
            lines.append(line)
    return lines + [f"Rule: {rule}." for rule in RULES]


def render(lines: list[str], overrides: str = "") -> str:
    card = "[[Turn card, hidden from players: " + " | ".join(lines) + "]] " if lines else ""
    # Control characters (a NUL makes subprocess refuse the tmux call) never reach the terminal.
    return re.sub(r"[\x00-\x1f\x7f]", " ", card + overrides)


def prepare(campaign_dir: Path, party_input: str = "", runtime_dir: Path | None = None):
    """(card, pulse): the card for this turn and the steering pulse to commit once it is delivered."""
    campaign_dir = Path(campaign_dir)
    if runtime_dir is None:
        runtime_dir = campaign_dir.parent.parent / ".runtime"
    try:
        pulse = arc_pulse.plan(campaign_dir, party_input)
    except Exception:  # steering is optional
        pulse = None
    try:
        return render(_lines(campaign_dir, party_input, runtime_dir, pulse), _roll_overrides(runtime_dir)), pulse
    except Exception:  # the card is optional; the party's reply must still reach the DM
        return "", pulse


def build(campaign_dir: Path, party_input: str = "", runtime_dir: Path | None = None) -> str:
    return prepare(campaign_dir, party_input, runtime_dir)[0]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    card = build(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "")
    print(card.replace(" | ", "\n  ") if card else "(no card: no state.md)")
