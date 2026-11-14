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
    "Never decide for the player characters: never spend their coin, hand over their things, or say yes to a deal, "
    "toll, fee or bribe they have not agreed to; name the price and stop",
    "Turn shape: one send.py call with every block of narration first (a player roll splits it: stake with "
    "the roll request, result after), bookkeeping in the next call, then reply done",
)
ROLL_MODES = {"auto": "you roll openly", "players": "players roll (call for the roll and wait)"}
CLOCK_WINDOW = 2
MAX_NPCS = 3
# Words that open a name without telling two people apart (#141): "Captain" or "Warden" comes up in play
# far more often than the person it heads.
TITLES = frozenset("""
    abbess abbot aunt bailiff baron baroness bishop brother captain chief commander constable count countess
    dame doctor duchess duke elder father general granny guard high king lady lieutenant lord madam madame
    magister magistrate master mayor mistress mother old priest priestess prince princess queen reeve saint
    sergeant sheriff sister steward uncle warden young
""".split())
PREP_SCRIPT = Path(__file__).resolve().parents[1] / "skills/dnd/scripts/turn_prep.py"
PREP_TIMEOUT = 5
# A clause in the state that leaves money or a deal open, waiting on the party's answer. An offer, an ask
# or a sum due counts only with money or a deal in the same clause. A payment settles its own clause; an
# answer (accepted, agreed, declined) also closes the offer before it. A best-effort hint: the rule is DM-CORE's.
OPEN = re.compile(r"\b(?:owed|owes|unpaid|(?:still|remains) due|no acceptance|not (?:yet )?(?:been )?"
                  r"(?:accepted|agreed|answered|paid|settled))\b", re.IGNORECASE)
ASKED = re.compile(r"\b(?:offer(?:s|ed)?|asks? for|asking for)\b", re.IGNORECASE)
SUM = r"\b\d+\s*(?:cp|sp|ep|gp|pp)\b"
DUE = re.compile(SUM + r"(?:\s+[\w'’-]+){0,2}\s+(?:is|are) (?:still )?due\b", re.IGNORECASE)
DEAL = re.compile(SUM + r"|\b(?:coins?|silver|gold|copper|fee|toll|fare|price|pay|payment|wages?|rent|bribe|reward|"
                  r"deal|job)\b", re.IGNORECASE)
PAID = re.compile(r"\b(?:paid|settled)\b", re.IGNORECASE)
ANSWERED = re.compile(r"\b(?:accepted|agreed|declined|refused|turned (?:it )?down)\b", re.IGNORECASE)
NEGATION = {"not", "never", "no", "none", "nothing", "nobody"}
# "must be paid", "to be settled", "the agreed fee": a payment or answer still to come, or a description.
NOT_DONE = {"be", "to", "the", "a", "an"}


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
    return (f"Trade policy (how they trade once they choose to; it never agrees a price, fee or deal for them): "
            f"{_clip(match.group(1), 220)}") if match else ""


def _negated(clause: str, match) -> bool:
    before = clause[:match.start()].lower().split()[-4:]
    return any(word in NEGATION or word.endswith("n't") for word in before)


def _done(clause: str, pattern) -> bool:
    for m in pattern.finditer(clause):
        before = clause[:m.start()].lower().split()
        if not _negated(clause, m) and not (before and before[-1] in NOT_DONE):
            return True
    return False


def _left_open(sentence: str) -> bool:
    waiting = False
    for clause in re.split(r";|,?\s+but\s+|,\s+(?:and|so)\s+", sentence):
        if _done(clause, ANSWERED):
            waiting = False
            continue
        if _done(clause, PAID):
            continue
        opened = next((m for m in OPEN.finditer(clause) if not _negated(clause, m)), None)
        asked = next((m for m in ASKED.finditer(clause) if not _negated(clause, m)), None)
        if opened or (asked and DEAL.search(clause)) or DUE.search(clause):
            waiting = True
    return waiting


def _waiting(state: str) -> str:
    found = []
    for entry in bullets(state, "Live State Flags") + bullets(state, "Current Situation"):
        found += [s for s in re.split(r"(?<=[.!?])\s+", entry) if _left_open(s)]
    if not found:
        return ""
    return ("Waiting on the party (theirs to answer: put it to them unless this input answers it, then resolve that; "
            "never pay, accept or hand over for them unasked): "
            + " / ".join(_clip(s, 160).rstrip(".") for s in found[:2]))


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


def _name_parts(name: str) -> list[str]:
    """The capitalised words of an entry's name that aren't titles, in order: "Odell", "Marsh"."""
    words = (word.strip(".,'’\"()") for word in name.split())
    return [w for w in words if len(w) >= 4 and w[0].isupper() and w.lower() not in TITLES]


def _mentioned(name: str, text: str, name_parts: dict[str, int]) -> bool:
    if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
        return True
    given = (_name_parts(name) or [""])[0]
    return bool(given) and name_parts.get(given.lower()) == 1 and bool(re.search(rf"\b{re.escape(given)}\b", text))


def _npcs(state: str, campaign_dir: Path, party_input: str) -> str:
    entries = _npc_entries(_read(campaign_dir / "npcs.md"))
    if not entries:
        return ""
    scene = " ".join((arc_pulse.location(state), field(state, "Current Situation", "Party status"), party_input))
    name_parts: dict[str, int] = {}  # a given name that is also someone's surname tells nobody apart
    for name in entries:
        for part in {p.lower() for p in _name_parts(name)}:
            name_parts[part] = name_parts.get(part, 0) + 1
    present = []
    for name, info in entries.items():
        if not _mentioned(name, scene, name_parts):
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
        lambda: _waiting(state),
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
