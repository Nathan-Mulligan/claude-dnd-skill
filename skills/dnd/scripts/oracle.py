"""
oracle.py — solo/GM oracle tools (chaos factor, yes/no, event focus, scene meaning).

Ported from the Neural Initiative engine's oracle module, re-implemented in the
skill's idiom (stdlib only; chaos factor persisted in the campaign's state.md
Session Flags rather than a DB row).

These are deterministic dice-driven tools, not LLM prompts. Wiring them in as
explicit commands stops the model from inventing its own pacing and gives the
table a transparent, rollable backbone for improvised play.

References:
  - Mythic GME 2e (Word Mill Games): chaos factor + Random Event Focus.
  - Ironsworn (Shawn Tomkin, CC-BY-4.0): yes/no oracle shape.
  - One Page Solo Engine: random scene-meaning word pairs.

Rolls use stdlib `random` so results are seedable for tests. `scripts/dice.py`
is available if you want physical-dice routing for the underlying d100, but the
oracle math stays here so verdicts are reproducible.

CLI:
  python3 oracle.py chaos        --campaign NAME            # show current factor
  python3 oracle.py chaos set    --campaign NAME --value N  # set factor (1-9)
  python3 oracle.py chaos adjust --campaign NAME --pc-won|--pc-lost
  python3 oracle.py ask  [--likelihood L] [--campaign NAME | --chaos N] [--seed S]
  python3 oracle.py event [--seed S]
  python3 oracle.py scene [--seed S]
  python3 oracle.py npc  [--trader] [--campaign NAME --name "Full Name"] [--seed S]
  python3 oracle.py npc note --campaign NAME --name "Full Name" "<one line>"
  python3 oracle.py road [--days N] [--campaign NAME] [--seed S]
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import re
import sys

from xp import CR_XP, MONSTER_MULTIPLIERS, XP_THRESHOLDS

try:
    import fcntl
except ImportError:  # Windows: no lock, the prepared day still works without one
    fcntl = None

try:
    from paths import find_campaign
except ImportError:  # pragma: no cover
    find_campaign = None  # type: ignore


# ── chaos factor (Mythic-style, 1-9) ─────────────────────────────────────────

CHAOS_MIN, CHAOS_MAX, CHAOS_DEFAULT = 1, 9, 5
_CHAOS_FLAG_RE = re.compile(r"^(\s*[-*]?\s*)chaos_factor:\s*([0-9]+)\s*$",
                            re.IGNORECASE)


def clamp_chaos(n: int) -> int:
    return max(CHAOS_MIN, min(CHAOS_MAX, n))


def adjust_chaos(factor: int, pc_proactive: bool) -> int:
    """Standard Mythic move: PC achieved scene goal → -1 (more in control);
    PC was reactive / failed → +1 (world pushes back). Clamped to [1, 9]."""
    return clamp_chaos(factor + (-1 if pc_proactive else 1))


def read_chaos(state_text: str) -> int:
    """Read `chaos_factor: N` from a state.md body. Default 5 if unset."""
    for line in state_text.splitlines():
        m = _CHAOS_FLAG_RE.match(line)
        if m:
            return clamp_chaos(int(m.group(2)))
    return CHAOS_DEFAULT


def write_chaos(state_text: str, value: int) -> str:
    """Return state.md text with `chaos_factor: N` set under ## Session Flags.

    Updates the line in place if present; otherwise inserts it just after the
    `## Session Flags` heading. If there is no Session Flags section, appends
    one at the end.
    """
    value = clamp_chaos(value)
    lines = state_text.splitlines()

    # In-place update if the flag already exists.
    for i, line in enumerate(lines):
        m = _CHAOS_FLAG_RE.match(line)
        if m:
            lines[i] = f"{m.group(1)}chaos_factor: {value}"
            return "\n".join(lines) + ("\n" if state_text.endswith("\n") else "")

    # Insert under an existing Session Flags heading.
    for i, line in enumerate(lines):
        if re.match(r"^##\s+Session Flags\s*$", line, re.IGNORECASE):
            lines.insert(i + 1, f"- chaos_factor: {value}")
            return "\n".join(lines) + ("\n" if state_text.endswith("\n") else "")

    # No section — append one.
    suffix = "" if state_text.endswith("\n") else "\n"
    return (state_text + suffix +
            f"\n## Session Flags\n- chaos_factor: {value}\n")


# ── yes/no oracle (Ironsworn-shaped) ─────────────────────────────────────────

_LIKELIHOODS = {
    "sure-thing": 90,
    "likely": 75,
    "50/50": 50,
    "unlikely": 25,
    "no-way": 10,
}


def yes_no(likelihood: str = "50/50", chaos: int = CHAOS_DEFAULT,
           rng: "random.Random | None" = None) -> "tuple[str, int]":
    """Return (verdict, roll). Verdict ∈ {yes, yes-and, yes-but, no, no-but, no-and}.

    Likelihood sets the base d100 target; chaos shifts it (high chaos widens the
    odds the world swings). Doubles → '-and' (extreme); within 10 of target →
    '-but' (qualified).
    """
    rng = rng or random.Random()
    base = _LIKELIHOODS.get(likelihood, 50)
    target = max(5, min(95, base + (chaos - CHAOS_DEFAULT) * 2))
    roll = rng.randint(1, 100)
    is_yes = roll <= target

    diff = abs(roll - target)
    rs = str(roll).zfill(2)
    if rs[0] == rs[1]:
        modifier = "-and"
    elif diff < 10:
        modifier = "-but"
    else:
        modifier = ""

    return ("yes" if is_yes else "no") + modifier, roll


# ── Random Event Focus (Mythic d100) ─────────────────────────────────────────

_FOCUS_TABLE = [
    (5, "remote event"),
    (10, "ambiguous event"),
    (20, "new NPC"),
    (35, "NPC action"),
    (45, "introduce thread"),
    (55, "move toward thread"),
    (65, "move away from thread"),
    (70, "close thread"),
    (80, "PC negative"),
    (85, "PC positive"),
    (95, "ambiguous event"),
    (100, "current context"),
]


def random_event_focus(rng: "random.Random | None" = None) -> "tuple[int, str]":
    """Mythic Random Event Focus. Returns (roll, focus_label). Treat the label
    as a *direction* to interpret against current threads, NPCs, and places."""
    rng = rng or random.Random()
    roll = rng.randint(1, 100)
    for threshold, label in _FOCUS_TABLE:
        if roll <= threshold:
            return roll, label
    return roll, "current context"


# ── scene-meaning word pairs (One Page Solo Engine) ──────────────────────────

_ACTIONS = [
    "abandon", "agree", "ambush", "approach", "arrive", "assist", "attach",
    "attain", "attract", "betray", "bind", "block", "break", "buy", "carry",
    "celebrate", "change", "command", "communicate", "conceal", "conclude",
    "construct", "contest", "create", "darken", "decay", "decrease", "defeat",
    "depart", "deprive", "destroy", "discover", "disturb", "dominate",
    "encourage", "endure", "energize", "equip", "expose", "fail", "fight",
    "flee", "forget", "forgive", "free", "gather", "give", "gladden",
    "guard", "guide", "harm", "heal", "hide", "hinder", "honour", "imitate",
    "increase", "inquire", "judge", "kill", "lead", "leave", "lighten",
    "love", "manipulate", "negate", "obey", "offer", "open", "oppose",
    "overcome", "pacify", "persecute", "praise", "preserve", "prevent",
    "pretend", "promise", "punish", "pursue", "reduce", "refuse", "release",
    "remember", "remove", "repulse", "reveal", "ruin", "save", "scheme",
    "seize", "share", "soothe", "spy", "start", "steal", "stop",
    "succeed", "suspect", "swear", "take", "threaten", "transform", "trick",
    "trust", "uncover", "undermine", "unify", "use", "venture",
    "violate", "wait", "warn", "weaken", "win", "wish", "wound", "yield",
]
_SUBJECTS = [
    "advice", "agreement", "animal", "armament", "art", "attention",
    "balance", "battle", "beauty", "benefit", "blood", "body", "bond",
    "book", "burden", "ceremony", "chaos", "child", "clothing", "code",
    "community", "competition", "conflict", "creation", "crime",
    "danger", "darkness", "death", "decision", "deed", "destiny", "device",
    "dialogue", "disaster", "disease", "doorway", "dream", "duty", "edge",
    "element", "emotion", "energy", "enemy", "entrance", "expectation",
    "expedition", "eye", "factor", "family", "famine", "fate", "fear",
    "festival", "fire", "fortune", "freedom", "friend", "garden", "gift",
    "gold", "good", "government", "health", "history", "home", "honour",
    "hope", "humour", "idea", "illusion", "image", "industry", "injustice",
    "intelligence", "investment", "joy", "justice", "key", "knowledge",
    "labour", "land", "language", "law", "lie", "life", "light", "luck",
    "magic", "marriage", "maze", "memory", "message", "messenger", "metal",
    "military", "mind", "mistake", "money", "music", "nature", "object",
    "oddity", "opposition", "outsider", "passage", "path", "peace", "people",
    "personality", "place", "plot", "ploy", "portent", "possessions",
    "poverty", "power", "project", "protection", "reality", "religion",
    "reputation", "resource", "rumour", "ruse", "sacrifice", "secret",
    "shadow", "shelter", "ship", "shore", "sign", "skill", "spirit",
    "stalemate", "structure", "success", "suffering", "surprise", "symbol",
    "task", "tension", "test", "thought", "time", "title", "tool",
    "transformation", "trap", "travel", "treasure", "trial", "trick",
    "truce", "truth", "unity", "value", "vehicle", "victory", "virtue",
    "void", "vow", "war", "warning", "way", "weapon", "weather", "wisdom",
    "wish", "world", "wound", "youth",
]


def scene_meaning(rng: "random.Random | None" = None) -> "tuple[str, str]":
    """Two-word generator: (action_verb, subject_noun). Feed to the narration
    when a scene runs dry or a Random Event Focus rolls."""
    rng = rng or random.Random()
    return rng.choice(_ACTIONS), rng.choice(_SUBJECTS)


# ── state.md helpers ─────────────────────────────────────────────────────────


def _resolve_state(name: str):
    if find_campaign is None:
        raise RuntimeError("paths.find_campaign unavailable; run from skill scripts dir")
    return find_campaign(name) / "state.md"


# ── improvised-campaign variety: NPC temperament and road days ───────────────
# Weighted 60/30/10 so most people are ordinary and a few stand out. One trait, played lightly.

_TEMPERAMENTS = (
    (60, "ordinary", ("busy and brisk", "friendly, a little chatty", "gruff but fair", "tired, wants this done",
                      "cautious with strangers", "plain-spoken", "proud of their work", "distracted by a worry at home",
                      "polite and forgettable", "quietly helpful")),
    (30, "colourful", ("sly, enjoys a bargain as a game", "anxious, over-explains", "warmly nosy", "dry joker",
                       "cheerful about grim things", "superstitious, reads small omens", "pompous about a small title",
                       "suspicious of outsiders until proven otherwise")),
    (6, "memorable", ("holds a grudge against someone the party has met", "quietly desperate for help they won't ask for",
                      "former adventurer with a limp and strong opinions", "collector who trades in stories as well as coin",
                      "knows more than they should about the current trouble")),
    (4, "troubled", ("grieving someone recently lost, and short with everyone", "in debt to the wrong people and jumpy about it",
                     "drinks a little too early in the day", "hiding an injury and won't say how they got it",
                     "estranged from their family and raw when it comes up")),
)
_WANTS_OWN = ("a fair deal and to get on with the day", "news from the road", "to be left alone",
              "to sell something they've had too long", "a bit of gossip", "reassurance about a worry",
              "to get home before dark", "a quiet word with someone they trust")
_WANTS_ASK = ("a letter carried to the next town on the party's road", "an escort as far as the next village",
              "help finding a strayed animal before nightfall", "someone to vouch for them with the local watch",
              "a hand mending or loading something heavy, paid in kind",
              "news of a relative who went down the road and stopped writing",
              "the party to buy something they can't shift", "a word put in for them with someone the party knows")
_TRADE_STANCES = ("names a fair price and barely haggles", "opens high and enjoys haggling", "needs the sale, flexible",
                  "won't move on price but throws in something extra", "wants proof of quality first",
                  "prefers a trade or favour to coin")

_ROAD_ROWS = {
    "quiet": ("Give it one vivid line and move on.", (
        "Drizzle all morning; by noon the cloud lifts over a long valley of hedged fields.",
        "A heron lifts out of a ditch as they pass; otherwise only wind in the grass.",
        "Cart ruts, a milestone with a worn name, smoke from a farmhouse chimney.",
        "Hot and still, dust on everything, and a clear stream to refill the waterskins at dusk.",
        "Frost on the blankets at dawn, then a clear cold day with the hills sharp on the horizon.",
        "A shepherd waves from a far slope and goes back to her flock.",
        "Fog until mid-morning, then a long bright afternoon of easy miles.",
        "Crows follow them for an hour, then lose interest.",
        "A warm wind smelling of cut hay, and a farm dog that trots alongside for a mile.",
        "Rain in the night; they wake to wet packs and a sky scrubbed clean.",
        "An old woman selling eggs at a gate, who wishes them a good road.",
        "The road climbs all day through pine and birch, and the night is cold and starry.",
    )),
    "traveller": ("Give them a temperament and a want (oracle.py npc --campaign <name> --name \"<Full Name>\"); "
                  "they bring news, a trade or a request.", (
        "A pedlar with a handcart, eager for news and selling needles, string and gossip.",
        "Pilgrims walking to a shrine, who ask to share the fire tonight.",
        "A patrol of the local lord's riders, asking who travels this road and why.",
        "A family moving everything they own on one cart, with a sick child in the back.",
        "A courier on a tired horse, asking how far it is to the next town.",
        "Two drovers whose cattle block the road, in no hurry to clear it.",
        "A tinker who mends pots and wants a lift for a day.",
        "A young runaway who claims to be older than they are.",
    )),
    "obstacle": ("Offer two real options with different costs.", (
        "The ford is swollen with rain: wait half a day, or risk the crossing.",
        "A fallen tree across the road: an hour's work to clear, or a muddy detour.",
        "A toll bridge whose bored keeper asks more than seems fair.",
        "Thick fog: slow down and lose time, or push on and risk losing the road.",
        "A mule goes lame: rest it, lighten the load, or buy another at the next farm.",
        "A washed-out stretch of road: the long way round, or a steep goat track.",
        "A wheel cracks on a stone: patch it here, or limp on to the next village.",
    )),
    "discovery": ("Investigating costs time; ignoring it may cost later.", (
        "An abandoned cart, one wheel off, its load gone and the harness cut.",
        "A roadside shrine with fresh offerings and a name newly scratched into the stone.",
        "Fresh tracks of several riders leaving the road for the woods.",
        "Smoke on the horizon, far too much for a cookfire.",
        "A ruined waystation whose cellar door someone has recently forced.",
        "A dead messenger bird with a capsule still tied to its leg.",
        "A ring of standing stones off the road, one of them newly toppled.",
    )),
    "danger": ("Telegraph the threat, and let talk, flight or a fight all work.", (
        "Bandits at a narrow bend, demanding a toll for safe passage.",
        "Wolves circling the camp at dusk, bold with hunger.",
        "A rival who followed them from the last town, with friends.",
        "A bear disturbed at a berry patch beside the road.",
        "Deserters camped at the ford, desperate and armed.",
        "Something with too many legs nesting in a stretch of old forest the road cuts through.",
        "Raiders driving off a farmer's sheep as the party passes.",
    )),
    "story": ("The story catches up with them: run it as a concrete event that demands a response.", (
        "A messenger or rumour tied to the current arc beat's world_pressure catches up with them.",
        "Someone from the last town arrives with bad news about an open thread.",
        "Signs on the road that a faction has made its move ahead of them.",
        "A person caught up in the current beat needs the party, now.",
        "The road passes the aftermath of something the current beat set in motion.",
    )),
}
_ROAD_WEIGHTS = {
    "mixed": (("quiet", 40), ("traveller", 20), ("obstacle", 15), ("discovery", 10), ("danger", 10), ("story", 5)),
    "adventure": (("quiet", 20), ("traveller", 18), ("obstacle", 15), ("discovery", 13), ("danger", 16), ("story", 18)),
    "downtime": (("quiet", 45), ("traveller", 28), ("obstacle", 10), ("discovery", 9), ("danger", 5), ("story", 3)),
}
ROAD_LOG = "road_log.json"
ROAD_NEXT = "road_next.json"
ROAD_LOCK = "road_next.lock"
_HELD: set = set()
ROAD_LOG_KEEP = 10

_DIFFICULTY_TIER = {"easy": 0, "standard": 1, "hard": 2, "deadly": 3}
_TIER_NAMES = ("easy", "medium", "hard", "deadly")
_EXAMPLE_FOES = {"0": "jackal", "1/8": "bandit", "1/4": "wolf", "1/2": "orc", "1": "dire wolf", "2": "ogre",
                 "3": "owlbear", "4": "wereboar", "5": "troll"}


def _weighted(table, rng):
    roll = rng.randint(1, sum(row[0] for row in table))
    for row in table:
        roll -= row[0]
        if roll <= 0:
            return row
    return table[-1]


def _norm_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def npc_rng(name: str) -> random.Random:
    """The same name always rolls the same person."""
    return random.Random("npc:" + _norm_name(name))


def npc_temperament(rng=None, trader: bool = False) -> dict:
    rng = rng or random.Random()
    _, tier, traits = _weighted(_TEMPERAMENTS, rng)
    temperament = rng.choice(traits)
    want = rng.choice(_WANTS_OWN + _WANTS_ASK)
    result = {"tier": tier, "temperament": temperament, "wants": want, "asks": want in _WANTS_ASK}
    if trader:
        result["trades"] = rng.choice(_TRADE_STANCES)
    return result


# ── state.md reading for the road ────────────────────────────────────────────

_TYPE_RE = re.compile(r"^\s*type:\s*[\"']?([A-Za-z]+)", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*\S)")
_CLOCK_RE = re.compile(r"^(?P<date>[^:]+?):\s*(?P<what>.+?)\s*(?:\(seen by:\s*(?P<seen>[^)]*?)\s*\))?\s*$")


def _section(text: str, title: str) -> str:
    m = re.search(rf"^##\s+{re.escape(title)}\s*$", text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return ""
    end = re.search(r"^##\s", text[m.end():], re.MULTILINE)
    return text[m.end(): m.end() + end.start()] if end else text[m.end():]


def _flag(text: str, name: str, default: str) -> str:
    m = re.search(rf"^\s*[-*]?\s*\**{name}\**\s*:\s*\**\s*([A-Za-z_]+)", text, re.MULTILINE | re.IGNORECASE)
    return m.group(1).lower() if m else default


def _bullets(text: str) -> list:
    return [m.group(1) for m in map(_BULLET_RE.match, text.splitlines()) if m]


def _parse_clock(line: str) -> dict:
    m = _CLOCK_RE.match(line)
    if not m:
        return {"date": "", "what": line, "seen_by": ""}
    return {"date": m.group("date").strip(), "what": m.group("what"), "seen_by": m.group("seen") or ""}


def campaign_kind(state_text: str):
    """The Campaign Arc `type:` (dynamic, sandbox, structured), or None when unset."""
    m = _TYPE_RE.search(_section(state_text, "Campaign Arc")) or _TYPE_RE.search(state_text)
    return m.group(1).lower() if m else None


def is_prebuilt(state_text: str) -> bool:
    return campaign_kind(state_text) == "structured"


def is_improvised(state_text: str) -> bool:
    return campaign_kind(state_text) in ("dynamic", "sandbox")


def read_road_context(state_text: str) -> dict:
    flags = _section(state_text, "Session Flags") or state_text
    return {
        "pacing": _flag(flags, "pacing", "mixed"),
        "difficulty": _flag(flags, "difficulty", "standard"),
        "hooks": _bullets(_section(state_text, "Next Hooks")),
        "clocks": [_parse_clock(line) for line in _bullets(_section(state_text, "Clocks"))],
    }


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def read_party(folder) -> "tuple[int, int] | None":
    """(party size, average level) from party_stats.json, else the character files."""
    try:
        data = json.loads((folder / "party_stats.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    players = data.get("players") if isinstance(data, dict) else None
    levels = [_int(p.get("level")) for p in players if isinstance(p, dict)] if isinstance(players, list) else []
    levels = [level for level in levels if level]
    if not levels:
        for path in sorted((folder / "characters").glob("*.md")):
            m = re.search(r"\*\*Level:\*\*\s*(\d+)", path.read_text(encoding="utf-8", errors="replace"))
            if m:
                levels.append(int(m.group(1)))
    if not levels:
        return None
    return len(levels), max(1, min(20, int(sum(levels) / len(levels) + 0.5)))


def _largest_cr(budget: int, count: int, party_size: int):
    steps = [mult for _, mult in MONSTER_MULTIPLIERS]
    idx = next(i for i, (limit, _) in enumerate(MONSTER_MULTIPLIERS) if count <= limit)
    if party_size < 3:
        idx = min(idx + 1, len(steps) - 1)
    elif party_size >= 6:
        idx = max(idx - 1, 0)
    best = None
    for cr, xp in CR_XP.items():
        if xp * count * steps[idx] > budget:
            break
        best = cr
    return best


def encounter_budget(party, difficulty: str = "standard") -> str:
    tier = _DIFFICULTY_TIER.get(difficulty, 1)
    if not party:
        return (f"Encounter budget: party unknown. Size it with xp.py calc --level <L> --players <N> "
                f"--difficulty {_TIER_NAMES[tier]}.")
    size, level = party
    budget = XP_THRESHOLDS[level][tier] * size
    options = []
    for count in (1, 2, 4):
        cr = _largest_cr(budget, count, size)
        if cr:
            foe = f" ({_EXAMPLE_FOES[cr]})" if cr in _EXAMPLE_FOES else ""
            options.append(f"{count} × CR {cr}{foe}")
    who = f"{size} character{'s' if size != 1 else ''} of level {level}"
    rough = f" Roughly {', '.join(options[:-1])} or {options[-1]}." if len(options) > 1 else (
        f" Roughly {options[0]}." if options else "")
    return f"Encounter budget ({_TIER_NAMES[tier]} for {who}): about {budget} XP.{rough}"


# ── the road ─────────────────────────────────────────────────────────────────


def _plot_texts(kind: str, context: dict, rng) -> list:
    """(source, text) pairs; the source lets one hook or clock be kept out of both story and traveller days."""
    hooks = [h.rstrip(".") for h in context.get("hooks", ())]
    clocks = context.get("clocks", ())
    if kind == "story":
        texts = [(f"hook:{h}", f"A hook ahead comes to meet them: {h}.") for h in hooks]
        for c in clocks:
            show = f"Show it on the road: {c['seen_by']}." if c["seen_by"] else "Show its first signs on the road."
            due = f" (due {c['date']})" if c["date"] else ""
            texts.append((f"clock:{c['what']}", f"A clock is ticking{due}: {c['what']}. {show}"))
        return texts
    if kind == "traveller" and (hooks or clocks) and rng.random() < 0.5:
        texts = [(f"clock:{c['what']}", f"Travellers who have seen it ({c['seen_by']}) bring news: {c['what']}"
                  f"{' (due ' + c['date'] + ')' if c['date'] else ''}.") for c in clocks if c["seen_by"]]
        return texts + [(f"hook:{h}", f"A traveller tied to what lies ahead: {h}.") for h in hooks]
    return []


def road_day(rng=None, pacing: str = "mixed", context=None, recent=()) -> dict:
    """One travel day. `recent` is the road log (oldest first): its texts are not reused,
    and the last day's kind does not come straight back unless it was quiet."""
    rng = rng or random.Random()
    last_kind = recent[-1]["kind"] if recent else None
    rows = [(weight, kind) for kind, weight in _ROAD_WEIGHTS.get(pacing, _ROAD_WEIGHTS["mixed"])
            if kind == "quiet" or kind != last_kind]
    _, kind = _weighted(rows, rng)
    used = [entry["text"] for entry in recent]
    used_sources = {entry.get("source") for entry in recent}
    plot = [(src, t) for src, t in _plot_texts(kind, context or {}, rng) if src not in used_sources and t not in used]
    day = {"kind": kind, "guidance": _ROAD_ROWS[kind][0]}
    if plot:
        day["source"], day["text"] = rng.choice(plot)
        return day
    examples = _ROAD_ROWS[kind][1]
    fresh = [t for t in examples if t not in used]
    # When a row is used up, the example seen longest ago is the least repetitive choice.
    day["text"] = rng.choice(fresh) if fresh else min(examples, key=lambda t: max(i for i, u in enumerate(used) if u == t))
    return day


def roll_road(days: int, rng=None, context=None, recent=(), party=None) -> list:
    rng = rng or random.Random()
    context = context or {}
    recent = list(recent)
    rolled = []
    for _ in range(days):
        day = road_day(rng=rng, pacing=context.get("pacing", "mixed"), context=context,
                       recent=recent[-ROAD_LOG_KEEP:])
        if day["kind"] == "danger":
            day["budget"] = encounter_budget(party, context.get("difficulty", "standard"))
        recent.append(_log_entry(day))
        rolled.append(day)
    return rolled


def _log_entry(day: dict) -> dict:
    return {key: day[key] for key in ("kind", "text", "source", "prep") if key in day}


def _read_road_log(folder) -> list:
    try:
        data = json.loads((folder / ROAD_LOG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict) and isinstance(e.get("kind"), str) and isinstance(e.get("text"), str)]


def _write_atomic(path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def calendar_day(cal) -> "list | None":
    """[year, month, day] of a calendar.json dict, the key a prepared road day is kept under."""
    try:
        return [int(cal["year"]), int(cal["month"]), int(cal["day"])]
    except (KeyError, TypeError, ValueError):
        return None


def _today(folder) -> "list | None":
    try:
        return calendar_day(json.loads((folder / "calendar.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


@contextlib.contextmanager
def road_lock(folder):
    """Held while a road day is prepared or used, and by calendar.py from reading the date to the last road roll."""
    key = os.path.realpath(folder)
    # flock is per open file, so a second open in the process holding it would wait forever.
    if fcntl is None or key in _HELD or not os.path.isdir(key):
        yield
        return
    with open(os.path.join(key, ROAD_LOCK), "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        _HELD.add(key)
        try:
            yield
        finally:
            _HELD.discard(key)
            fcntl.flock(handle, fcntl.LOCK_UN)


def _road_context(folder) -> dict:
    state = folder / "state.md"
    return read_road_context(state.read_text(encoding="utf-8") if state.is_file() else "")


def _still_current(day: dict, context: dict) -> bool:
    """False once the hook or clock a story or traveller day was built on has left state.md."""
    source = day.get("source")
    if not source:
        return True
    current = {f"hook:{h.rstrip('.')}" for h in context.get("hooks", ())}
    current |= {f"clock:{c['what']}" for c in context.get("clocks", ())}
    return source in current


def _read_prepared(folder) -> "dict | None":
    try:
        kept = json.loads((folder / ROAD_NEXT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    day = kept.get("day") if isinstance(kept, dict) else None
    if isinstance(day, dict) and isinstance(day.get("kind"), str) and isinstance(day.get("text"), str):
        return kept
    return None


def _usable(kept, date, context: dict, log: list) -> bool:
    """A kept day is used only on the date it was rolled for, while its story source stands, and only once."""
    if not kept or kept.get("for") != date or not _still_current(kept["day"], context):
        return False
    return not any(entry.get("prep") == kept["day"].get("prep") for entry in log)


def prepare_road(folder, rng=None) -> dict:
    """The next travel day, rolled once and kept in road_next.json until a road roll uses it.

    Never logged and never moves the calendar, so asking again shows the same day."""
    with road_lock(folder):
        today, context, log = _today(folder), _road_context(folder), _read_road_log(folder)
        kept = _read_prepared(folder)
        if _usable(kept, today, context, log):
            return kept["day"]
        rng = rng or random.Random()
        day = roll_road(1, rng=rng, context=context, recent=log, party=read_party(folder))[0]
        day["prep"] = "%016x" % rng.getrandbits(64)
        _write_atomic(folder / ROAD_NEXT, json.dumps({"for": today, "day": day}, indent=2, ensure_ascii=False) + "\n")
        return day


def campaign_road(folder, days: int, rng=None, start=None) -> list:
    """Roll `days` travel days for the campaign in `folder` and record them in road_log.json.

    A day kept by prepare_road for the date the journey starts (`start`, [year, month, day];
    default today) becomes day 1, so a prepared day is never rolled twice. The kept day is
    deleted only after the log holds it, so a failed write never loses it."""
    with road_lock(folder):
        log, context = _read_road_log(folder), _road_context(folder)
        kept = _read_prepared(folder) if days > 0 else None
        date = start if start is not None else _today(folder)
        rolled = [kept["day"]] if _usable(kept, date, context, log) else []
        rolled += roll_road(days - len(rolled), rng=rng, context=context,
                            recent=log + [_log_entry(d) for d in rolled], party=read_party(folder))
        log += [_log_entry(d) for d in rolled]
        _write_atomic(folder / ROAD_LOG, json.dumps(log[-ROAD_LOG_KEEP:], indent=2, ensure_ascii=False) + "\n")
        later = isinstance(date, list) and isinstance(kept and kept.get("for"), list) and kept["for"] > date
        if kept and not later:
            (folder / ROAD_NEXT).unlink(missing_ok=True)
        return rolled


def format_road_day(day: dict, number: int, total: int, label: str = "") -> str:
    when = f" ({label})" if label else ""
    line = f"Day {number} of {total}{when}: {day['kind']}. {day['text']} {day['guidance']}"
    return line + (f"\n    {day['budget']}" if day.get("budget") else "")


# ── npcs.md entries ──────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_ENTRY_SUBSECTIONS = {"personality", "relationships", "notes"}


_ANNOTATION_STARTS = (" (", " —", " –", " - ", ",", ":")


def _heading_is(text: str, name: str, exact: bool) -> bool:
    """Exact name, or (exact=False) the name followed by an annotation such as "(trader)" or "— innkeeper"."""
    heading, wanted = _norm_name(text.strip("* ")), _norm_name(name)
    return heading == wanted or (not exact and heading.startswith(tuple(wanted + a for a in _ANNOTATION_STARTS)))


def _is_entry_heading(match) -> bool:
    return match.group(2).strip("* ").casefold() not in _ENTRY_SUBSECTIONS


def _entry_span(lines: list, name: str):
    for exact in (True, False):
        for i, line in enumerate(lines):
            m = _HEADING_RE.match(line)
            if not m or not 2 <= len(m.group(1)) <= 4 or not _heading_is(m.group(2), name, exact):
                continue
            level = len(m.group(1))
            for j in range(i + 1, len(lines)):
                h = _HEADING_RE.match(lines[j])
                if h and len(h.group(1)) <= level and _is_entry_heading(h):
                    return i, j
            return i, len(lines)
    return None


def _ensure_entry(lines: list, name: str):
    span = _entry_span(lines, name)
    if span:
        return span
    levels = {len(m.group(1)) for m in map(_HEADING_RE.match, lines) if m and _is_entry_heading(m)}
    marker = "##" if 2 in levels and 3 not in levels else "###"
    while lines and not lines[-1].strip():
        lines.pop()
    lines += ["", f"{marker} {name}"]
    return len(lines) - 1, len(lines)


def _read_npcs(path) -> list:
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else ["# NPCs"]


def save_npc_fields(path, name: str, fields) -> None:
    """Set `- **Label:** value` lines in the NPC's entry, adding the entry if it is missing."""
    lines = _read_npcs(path)
    start, end = _ensure_entry(lines, name)
    insert_at = start + 1
    while insert_at < end and _BULLET_RE.match(lines[insert_at]):
        insert_at += 1
    for label, value in fields:
        prefix = f"- **{label}:**"
        existing = next((k for k in range(start + 1, end) if lines[k].startswith(prefix)), None)
        if existing is not None:
            lines[existing] = f"{prefix} {value}"
        else:
            lines.insert(insert_at, f"{prefix} {value}")
            insert_at += 1
            end += 1
    _write_atomic(path, "\n".join(lines) + "\n")


def add_npc_note(path, name: str, note: str, date: str = "") -> str:
    lines = _read_npcs(path)
    start, end = _ensure_entry(lines, name)
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    line = f"- Party note ({date}): {note}" if date else f"- Party note: {note}"
    lines.insert(end, line)
    _write_atomic(path, "\n".join(lines) + "\n")
    return line


def _world_date(folder) -> str:
    try:
        cal = json.loads((folder / "calendar.json").read_text(encoding="utf-8"))
        months = cal.get("months") or []
        month = cal.get("month", 1)
        name = months[month - 1] if 1 <= month <= len(months) else f"Month {month}"
        return f"{cal.get('day', 1)} {name} {cal.get('year', 1)}"
    except (OSError, ValueError, AttributeError, TypeError):
        return ""


# ── CLI ──────────────────────────────────────────────────────────────────────


def cmd_chaos(args) -> int:
    state_path = _resolve_state(args.campaign)
    text = state_path.read_text(encoding="utf-8") if state_path.exists() else ""
    current = read_chaos(text)

    if args.chaos_action == "set":
        new = clamp_chaos(args.value)
        state_path.write_text(write_chaos(text, new), encoding="utf-8")
        print(f"chaos factor: {current} → {new}")
        return 0
    if args.chaos_action == "adjust":
        if args.pc_won == args.pc_lost:
            print("error: pass exactly one of --pc-won / --pc-lost", file=sys.stderr)
            return 2
        new = adjust_chaos(current, pc_proactive=args.pc_won)
        state_path.write_text(write_chaos(text, new), encoding="utf-8")
        verb = "PC in control" if args.pc_won else "world pushes back"
        print(f"chaos factor: {current} → {new}  ({verb})")
        return 0
    # default: show
    print(f"chaos factor: {current}  (1=PCs in control, 9=world in chaos)")
    return 0


def _chaos_for(args) -> int:
    """Resolve a chaos value for ask: explicit --chaos wins, else read campaign,
    else neutral default."""
    if args.chaos is not None:
        return clamp_chaos(args.chaos)
    if args.campaign:
        state_path = _resolve_state(args.campaign)
        if state_path.exists():
            return read_chaos(state_path.read_text(encoding="utf-8"))
    return CHAOS_DEFAULT


def cmd_ask(args) -> int:
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    chaos = _chaos_for(args)
    verdict, roll = yes_no(args.likelihood, chaos, rng=rng)
    print(f"{verdict.upper()}  (d100={roll}, likelihood={args.likelihood}, chaos={chaos})")
    return 0


def cmd_event(args) -> int:
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    roll, label = random_event_focus(rng=rng)
    print(f"event focus: {label}  (d100={roll})")
    return 0


def cmd_scene(args) -> int:
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    action, subject = scene_meaning(rng=rng)
    print(f"scene meaning: {action} / {subject}")
    return 0


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


def _campaign_folder(name: str):
    if find_campaign is None:
        raise RuntimeError("paths.find_campaign unavailable; run from skill scripts dir")
    folder = find_campaign(name)
    if not folder.is_dir():
        print(f"error: no campaign folder for {name!r}", file=sys.stderr)
        return None
    return folder


def cmd_npc(args) -> int:
    if args.npc_action == "note":
        return cmd_npc_note(args)
    name = " ".join((args.name or "").split())
    if args.campaign and not name:
        print("error: --campaign needs --name, so the result can be saved under that NPC", file=sys.stderr)
        return 2
    folder = _campaign_folder(args.campaign) if args.campaign else None
    if args.campaign and folder is None:
        return 2
    if name:
        rng = npc_rng(name)
    else:
        rng = random.Random(args.seed) if args.seed is not None else random.Random()
    npc = npc_temperament(rng=rng, trader=args.trader)
    want = npc["wants"] + (" (an ask: have them put it to the party)" if npc["asks"] else "")
    if name:
        print(name)
    print(f"temperament ({npc['tier']}): {npc['temperament']}")
    print(f"wants right now: {want}")
    if "trades" in npc:
        print(f"trades: {npc['trades']}")
    if folder is not None:
        fields = [("Temperament", f"{npc['temperament']} ({npc['tier']})"), ("Wants now", want)]
        if "trades" in npc:
            fields.append(("Trades", npc["trades"]))
        save_npc_fields(folder / "npcs.md", name, fields)
        print(f"Saved to npcs.md under {name}. Play one trait lightly and keep it every time they appear.")
    else:
        print("Play one trait lightly and keep it every time they appear. "
              "Pass --campaign and --name to save it in npcs.md.")
    return 0


def cmd_npc_note(args) -> int:
    name = " ".join(args.name.split())
    note = " ".join(" ".join(args.text).split())
    if not name or not note:
        print("error: npc note needs --name and a non-empty note", file=sys.stderr)
        return 2
    folder = _campaign_folder(args.campaign)
    if folder is None:
        return 2
    print(add_npc_note(folder / "npcs.md", name, note, _world_date(folder)))
    return 0


def cmd_road(args) -> int:
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    if not args.campaign:
        days = roll_road(args.days, rng=rng)
    else:
        folder = _campaign_folder(args.campaign)
        state_path = folder / "state.md" if folder else None
        if state_path is None or not state_path.is_file():
            print(f"error: no state.md for campaign {args.campaign!r}", file=sys.stderr)
            return 2
        if is_prebuilt(state_path.read_text(encoding="utf-8")):
            print("Prebuilt campaign: use the module's own travel and wandering-encounter rules, not this table.")
            return 0
        days = campaign_road(folder, args.days, rng=rng)
    for number, day in enumerate(days, 1):
        print(format_road_day(day, number, len(days)))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="oracle", description=__doc__.split("\n", 2)[1])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("chaos", help="get/set/adjust the campaign chaos factor (1-9)")
    sp.add_argument("chaos_action", nargs="?", choices=["show", "set", "adjust"],
                    default="show")
    sp.add_argument("--campaign", required=True)
    sp.add_argument("--value", type=int, help="for `set`: the new factor (1-9)")
    sp.add_argument("--pc-won", action="store_true",
                    help="for `adjust`: PC achieved the scene goal → -1")
    sp.add_argument("--pc-lost", action="store_true",
                    help="for `adjust`: PC was reactive / failed → +1")
    sp.set_defaults(func=cmd_chaos)

    sp = sub.add_parser("ask", help="yes/no oracle (Ironsworn-shaped)")
    sp.add_argument("--likelihood", default="50/50", choices=list(_LIKELIHOODS),
                    help="odds before chaos modifier")
    sp.add_argument("--campaign", help="read chaos factor from this campaign")
    sp.add_argument("--chaos", type=int, help="explicit chaos factor (overrides --campaign)")
    sp.add_argument("--seed", type=int, help="seed the d100 (reproducible)")
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("event", help="Mythic Random Event Focus (d100)")
    sp.add_argument("--seed", type=int)
    sp.set_defaults(func=cmd_event)

    sp = sub.add_parser("scene", help="random scene-meaning word pair")
    sp.add_argument("--seed", type=int)
    sp.set_defaults(func=cmd_scene)

    sp = sub.add_parser("npc", help="temperament and current want for an NPC (improvised campaigns)")
    sp.add_argument("--trader", action="store_true", help="also roll how they trade")
    sp.add_argument("--campaign", help="with --name: save the result in this campaign's npcs.md")
    sp.add_argument("--name", help="the NPC's full name; seeds the roll, so a name always gives the same person")
    sp.add_argument("--seed", type=int, help="seed an unnamed roll")
    sp.set_defaults(func=cmd_npc, npc_action=None)
    npc_sub = sp.add_subparsers(dest="npc_action")
    note = npc_sub.add_parser("note", help="append one line about how a meeting with the party went")
    note.add_argument("--campaign", required=True)
    note.add_argument("--name", required=True, help="the NPC's full name, as in npcs.md")
    note.add_argument("text", nargs="+", help='e.g. "Sold 4 blankets at 18. Thinks they drive a hard bargain."')

    sp = sub.add_parser("road", help="what each travel day brings (improvised campaigns)")
    sp.add_argument("--days", type=_positive_int, default=1)
    sp.add_argument("--campaign", help="use this campaign's pacing, hooks, clocks, party and road log; "
                                       "skipped for prebuilt (structured) campaigns")
    sp.add_argument("--seed", type=int)
    sp.set_defaults(func=cmd_road)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
