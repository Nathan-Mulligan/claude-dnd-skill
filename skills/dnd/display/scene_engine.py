"""The one scene engine that scene music and background images draw from.

A DM cue names a place and what is happening. Everything else is derived here:
a stable key, the broad setting, the era and place-theme path from the
campaign's world notes, the mood, and the music and image prompts.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

SETTINGS = frozenset({
    "arcane", "castle", "cave", "city", "crypt", "desert", "dungeon",
    "fire", "forest", "mine", "mountain", "night", "ocean", "prison",
    "ruins", "swamp", "tavern", "temple",
})
SETTING_ALIASES = {"tower": "arcane"}
MOODS = frozenset({"calm", "curious", "ominous", "tense", "warm"})
ERAS = ("medieval", "renaissance", "ancient", "modern", "futuristic")


MOOD_PROFILES = {
    "warm": {"atmosphere": "Welcoming, happy and gently playful fantasy atmosphere.",
             "visual": "Muted leaf greens, warm amber and soft golden light in the scenery.",
             "music": "Consonant acoustic folk phrasing, gentle lilting melody and warm harmonies."},
    "ominous": {"atmosphere": "Shadowed, uncanny dark fantasy atmosphere.",
                "visual": "Cool slate blues, deep violet shadows and sparse restrained amber light.",
                "music": "Restrained minor or suspended harmonies, low strings and distant hollow bells."},
    "curious": {"atmosphere": "Curious fantasy wonder with a light sense of mystery.",
                "visual": "Balanced muted teal and brass, soft pools of light and inviting shaded detail.",
                "music": "Light plucked phrases, airy woodwind answers and gently exploring motifs."},
    "calm": {"atmosphere": "Peaceful, tranquil and unhurried fantasy atmosphere.",
             "visual": "Soft sage, muted blue and lavender, diffused light and gentle edges.",
             "music": "Long gentle melodic sustains, sparse harp and settled quiet harmonies."},
    "tense": {"atmosphere": "Tense fantasy anticipation with restrained urgency.",
              "visual": "Desaturated umber, cool shadows and small restrained red accents.",
              "music": "Held string figures and a restrained uneven pulse without harsh stingers."},
}


@dataclass(frozen=True)
class PlaceTheme:
    name: str
    mood: str | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 100 or "\0" in self.name:
            raise ValueError("invalid place theme name")
        if self.mood is not None and (not isinstance(self.mood, str) or self.mood not in MOODS):
            raise ValueError("invalid place theme mood")

    def to_dict(self) -> dict:
        return {"name": self.name} | ({"mood": self.mood} if self.mood is not None else {})


@dataclass(frozen=True)
class SceneContext:
    key: str
    label: str
    description: str
    theme: tuple[PlaceTheme, ...] | None = None
    era: str | None = None

    @classmethod
    def parse(cls, value: dict | SceneContext | None) -> SceneContext | None:
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        required = {"key", "label", "description"}
        if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {"theme", "era"}:
            raise ValueError("scene requires key, label and description")
        if not all(isinstance(value[key], str) for key in required):
            raise ValueError("scene fields must be text")
        theme = None
        if "theme" in value:
            levels = value["theme"]
            if not isinstance(levels, list) or len(levels) > 8:
                raise ValueError("scene theme must contain at most eight places")
            parsed = []
            for level in levels:
                if not isinstance(level, dict) or "name" not in level or set(level) - {"name", "mood"}:
                    raise ValueError("place theme requires a name and optional mood")
                if not isinstance(level["name"], str):
                    raise ValueError("place theme name must be text")
                parsed.append(PlaceTheme(level["name"].strip(), level.get("mood")))
            theme = tuple(parsed)
        era = None
        if "era" in value:
            era = value["era"]
            if not isinstance(era, str) or not era.isprintable():
                raise ValueError("invalid scene era")
            era = era.strip()
        return cls(**{key: value[key].strip() for key in required}, theme=theme, era=era)

    def __post_init__(self):
        if not isinstance(self.key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", self.key):
            raise ValueError("invalid scene key")
        for text, limit in ((self.label, 120), (self.description, 800)):
            if not isinstance(text, str) or not text.strip() or len(text) > limit or "\0" in text:
                raise ValueError("invalid scene label or description")
        if self.theme is not None and (not isinstance(self.theme, tuple) or len(self.theme) > 8 or
                                       not all(isinstance(level, PlaceTheme) for level in self.theme)):
            raise ValueError("invalid scene theme")
        if self.era is not None and (not isinstance(self.era, str) or not self.era.strip() or
                                     len(self.era) > 100 or not self.era.isprintable()):
            raise ValueError("invalid scene era")

    def to_dict(self) -> dict:
        value = {"key": self.key, "label": self.label, "description": self.description}
        if self.theme is not None:
            value["theme"] = [level.to_dict() for level in self.theme]
        if self.era is not None:
            value["era"] = self.era
        return value

    def place_guidance(self) -> str:
        if not self.theme:
            return ""
        return "Place ancestry: " + _ancestry(self.theme) + ". The resolved atmosphere below takes precedence over ancestor tones. "


def _ancestry(theme: tuple[PlaceTheme, ...]) -> str:
    return " > ".join(level.name for level in theme)


def scene_era(scene: SceneContext | None) -> str:
    return scene.era if scene and scene.era else "medieval"


def theme_mood(theme: tuple[PlaceTheme, ...] | None) -> str | None:
    return next((level.mood for level in reversed(theme or ()) if level.mood is not None), None)


# ─── Music prompts ───────────────────────────────────────────────────────────

# Each setting supplies a default tone; an explicit/inherited mood overrides it.
SCENE_MUSIC = {
    "arcane": ("Arcane tower", "curious", "Measured, intricate and subtly strange."),
    "castle": ("Castle", "calm", "Measured, steady and dignified."),
    "cave": ("Gloomy cavern", "ominous", "Low, hollow and shadowed, with slow movement."),
    "city": ("City", "warm", "Everyday activity and a steady pace."),
    "crypt": ("Ancient crypt", "ominous", "Still, shadowed and restrained."),
    "desert": ("Desert journey", "calm", "Open, dry and unhurried."),
    "dungeon": ("Dungeon", "ominous", "Low, shadowed and restrained."),
    "fire": ("Firelit", "tense", "A steady pulse and a sense of approaching danger."),
    "forest": ("Calm woodland", "calm", "Grounded, spacious and gently flowing."),
    "mine": ("Old mine", "ominous", "Deep stone passages and distant activity."),
    "mountain": ("Mountain journey", "calm", "Open, steady and dignified, with a sense of distance."),
    "night": ("Night journey", "calm", "Quiet, measured and unhurried."),
    "ocean": ("Ocean journey", "calm", "Broad, gently flowing and steady."),
    "prison": ("Prison", "ominous", "Confined, subdued and watchful."),
    "ruins": ("Ancient ruins", "curious", "Weathered stone, forgotten history and quiet mystery."),
    "swamp": ("Swamp", "ominous", "Low, murky and slow-moving."),
    "tavern": ("Medieval tavern", "warm", "A friendly but aged roadside inn."),
    "temple": ("Sacred sanctuary", "calm", "Still, reverent and softly resonant."),
}
MUSIC_MOODS = {
    "calm": "Grounded, spacious and gently flowing.",
    "curious": "Measured and quietly mysterious.",
    "ominous": "Low, shadowed and restrained.",
    "tense": "Restrained unease, a steady pulse and a sense of approaching danger.",
    "warm": "Understated warmth and an easygoing pace.",
}
BATTLE_PROMPT = (
    "Medieval fantasy battle music with frame drum. Driving rhythm, "
    "forceful bowed string phrases. Urgent and determined."
)


def music_prompt(setting: str, mood: str, kind: str, scene: SceneContext | None = None) -> str:
    era = scene_era(scene)
    if kind == "combat":
        return BATTLE_PROMPT.replace("Medieval", era.capitalize(), 1)
    title, default_mood, tone = SCENE_MUSIC[setting]
    if mood != default_mood:
        title = title.removeprefix("Calm ").removeprefix("Gloomy ")
        tone = MUSIC_MOODS[mood]
    if era != "medieval":
        title = title.removeprefix("Medieval ")
    prefix = f"{title} fantasy music. {tone}"
    if kind == "bed":
        purpose = (f"Atmospheric {era} instrumental background music with sparse frame drum. "
                   "Spacious and understated, long spaces between melodies.")
    else:
        purpose = f"{era.capitalize()} instrumental music with frame drum."
    context = ""
    if scene:
        context = f" Scene: {scene.label}. {scene.description}"
        if scene.theme:
            context += " Place ancestry: " + _ancestry(scene.theme) + "."
    return f"{prefix} {purpose}{context}".strip()


# ─── Image prompts ───────────────────────────────────────────────────────────

ENVIRONMENTS = {
    "arcane": "an ancient magical observatory, floating stone arches and luminous runes",
    "castle": "a medieval castle courtyard, tall stone towers and distant battlements",
    "cave": "a vast vaulted cavern, stalactites, an underground lake and ancient ruined stone archways",
    "city": "a medieval harbour city, distinctive rooftops, towers and narrow streets",
    "crypt": "an underground stone crypt, vaulted tombs and old carved pillars",
    "desert": "a desert of rolling dunes, distant sandstone ruins and an oasis",
    "dungeon": "an underground dungeon of damp stone corridors, iron bars and torchlit arches",
    "fire": "a volcanic landscape, a recognisable volcanic peak, lava rivers and dark basalt cliffs",
    "forest": "an ancient forest, layered woodland, mossy trunks and a distant stone bridge",
    "mine": "an abandoned mine, timber supports, rock tunnels and old rail tracks",
    "mountain": "a mountain valley, distant jagged peaks and a stone fortress on a ridge",
    "night": "a quiet moonless countryside, distant village rooftops and dark wooded hills",
    "ocean": "a coastal seascape, broad open sea, weathered cliffs and a distant harbour village",
    "prison": "a medieval underground prison, barred stone cells and torchlit passages",
    "ruins": "an ancient ruined city, broken stone towers and overgrown arches",
    "swamp": "a misty swamp, twisted trees, still water and an old sunken stone causeway",
    "tavern": "a warm medieval roadside inn, timber beams, a hearth and a distant bar",
    "temple": "an ancient temple, monumental stone columns, broad stairs and a distant altar",
}

ERA_GUIDANCE = {
    "medieval": "A pre-industrial medieval fantasy world, historically inspired rather than contemporary. Period material culture throughout: rough-hewn timber, limewashed stone, hand-forged iron, pottery, wooden furnishings, beeswax candles and hearth light. ",
    "renaissance": "A Renaissance-inspired fantasy world with carved stone and timber architecture, period furnishings, oil lamps and candles. ",
    "ancient": "An ancient-world fantasy setting with era-appropriate stone, plaster and timber architecture, pottery and oil lamps. ",
    "modern": "An explicitly modern world with contemporary architecture, furnishings, technology appropriate to the described scene. ",
    "futuristic": "An explicitly futuristic world with advanced architecture, technology, materials appropriate to the described scene. ",
}


def image_prompt(setting: str, mood: str, reference: bool = False, density_map: bool = False,
                 scene: SceneContext | None = None) -> str:
    opening = ("Use the reference only for its painterly style, composition and gradual shading. "
               "Replace its entire environment with the requested setting. Do not copy cave walls, "
               "stalactites or buildings unless they belong in the requested setting. " if reference else "")
    if density_map:
        opening = ("The reference is a grayscale detail density map, not a literal scene. White means "
                   "scenery and detail are allowed in the upper area and outer edges. Black means a quiet "
                   "dark reading area in the lower centre. Follow that composition, but create a full-colour "
                   "painting of the requested environment, not a grayscale image or a visible map. ")
    environment = scene.description if scene else ENVIRONMENTS[setting]
    profile = MOOD_PROFILES[mood]
    era = scene_era(scene)
    fantasy_era = era.casefold() in {"medieval", "renaissance", "ancient"}
    atmosphere = profile["atmosphere"] if fantasy_era else profile["atmosphere"].replace("fantasy ", "")
    tone = (scene.place_guidance() + atmosphere + " " + profile["visual"] + " "
            if scene else "")
    palette = ("Subdued saturation and gentle scenic contrast, with no glaring highlights. " if tone else
               "Subtle muted ink blue, teal, stone grey and restrained amber, low saturation and very low tonal contrast, dim light, no bright highlights. ")
    guidance = ERA_GUIDANCE.get(era.casefold(),
        f"The established world era is {era}. Use architecture, materials and technology appropriate to that era and the described scene. ")
    genre = "fantasy " if fantasy_era else ""
    return (
        "A scenery-first environment establishing shot, focused on landscape and architecture. "
        "By default, depict no people, characters, creatures or monsters. The only exception is a specific "
        "character, NPC, creature or monster that the scene explicitly says the party is currently interacting with. "
        "If no such active interaction is described, show scenery only. A mere mention of a person or animal "
        "in the setting is not an interaction. Include only the subject of that explicit interaction, "
        "subordinate to the scenery and clear of the central reading area. Any permitted interaction subject "
        "must fit the established world era. " + guidance +
        "Preserve any fantastical machines, constructs or other exceptional elements explicitly described in this world. " +
        opening + f"Paint a very wide shallow illustrated {genre}panorama of {environment}. " + tone +
        f"{mood.capitalize()} atmosphere. Recognisable distant scenery, soft painterly forms and shaded depth. "
        "Place the horizon and major landmarks in the upper quarter, with restrained framing at the outer edges. "
        "The lower three quarters and central reading area are a dark reading area, quiet low-contrast shadow with subtle visible "
        "surface detail. Blend the scenery gradually into that reading area, with no abrupt black band or "
        "solid rectangle. " + palette + "Landmarks are distant and small, not large "
        "focal subjects. Matte storybook painting that supports narration rather than dominating it. "
        "No bystanders, background people, crowds, unrelated wildlife or extra monsters. No portraits, close-up figures, text, lettering, interface, borders, sun, stars or decorative clouds."
    )


# ─── World notes ─────────────────────────────────────────────────────────────

_ERA_LINE = re.compile(r"^[\s>*-]*(?:\*\*)?(?:established era|world era|era)(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.+)$",
                       re.IGNORECASE | re.MULTILINE)
_SETTING_TYPE_LINE = re.compile(r"^[\s>*-]*(?:\*\*)?setting type(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.+)$",
                                re.IGNORECASE | re.MULTILINE)
_JSON_ROOT = re.compile(r"\[\s*\{.*?\}\s*\]")
_JSON_CHILD = re.compile(r"\bchild\s*(\{.*?\})", re.IGNORECASE)
_ARROW_THEME = re.compile(r"scene theme[^:\n]*:\s*([^;\n]+)", re.IGNORECASE)
_ARROWS = re.compile(r"\s*(?:→|->|>)\s*")
_NODE = re.compile(r"^(.*?)\s*(?:\((\w+)\))?\s*$")


def _era_from(value: str, custom: bool) -> str | None:
    value = value.strip().strip("*").strip()
    if not value or value.startswith("<"):
        return None
    first = re.split(r"[.;,]\s|[.;,]$|\s+with\s+", value, maxsplit=1)[0].strip()
    words = set(re.findall(r"[a-z]+", first.casefold()))
    known = next((era for era in ERAS if era in words), None)
    if known or not custom:
        return known
    return first if 0 < len(first) <= 100 and first.isprintable() else None


def _arrow_path(text: str) -> tuple[PlaceTheme, ...]:
    path = []
    for part in _ARROWS.split(text.strip().rstrip(".")):
        name, mood = _NODE.match(part.strip()).groups()
        if name:
            path.append(PlaceTheme(name[:100], mood if mood in MOODS else None))
    return tuple(path[:8])


def _json_paths(line: str) -> list[tuple[PlaceTheme, ...]]:
    root_match = _JSON_ROOT.search(line)
    if not root_match:
        return []
    root = tuple(PlaceTheme(level["name"].strip(), level.get("mood")) for level in json.loads(root_match.group()))
    if len(root) > 8:
        return []
    paths = [root]
    for child in _JSON_CHILD.findall(line[root_match.end():]):
        level = json.loads(child)
        paths.append((root + (PlaceTheme(level["name"].strip(), level.get("mood")),))[:8])
    return paths


@dataclass(frozen=True)
class WorldNotes:
    """The parts of a campaign's world.md that the scene engine reads."""

    era: str | None = None
    places: tuple[tuple[PlaceTheme, ...], ...] = ()

    @classmethod
    def parse(cls, text: str) -> WorldNotes:
        era = next(filter(None, (_era_from(m.group(1), True) for m in _ERA_LINE.finditer(text))), None)
        if era is None:
            era = next(filter(None, (_era_from(m.group(1), False) for m in _SETTING_TYPE_LINE.finditer(text))), None)
        places: dict[tuple[str, ...], tuple[PlaceTheme, ...]] = {}
        for line in text.splitlines():
            found: list[tuple[PlaceTheme, ...]] = []
            try:
                found += _json_paths(line)
                found += [_arrow_path(m.group(1)) for m in _ARROW_THEME.finditer(line)]
            except (ValueError, TypeError, AttributeError, KeyError):
                continue
            for path in found:
                for depth in range(1, len(path) + 1):
                    names = tuple(_norm(level.name) for level in path[:depth])
                    if all(names):
                        places.setdefault(names, path[:depth])
        return cls(era, tuple(places.values()))

    @classmethod
    def load(cls, campaign_dir: Path) -> WorldNotes:
        try:
            return cls.parse((campaign_dir / "world.md").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return cls()


# ─── Cue derivation ──────────────────────────────────────────────────────────

SETTING_KEYWORDS = {
    "tavern": ("tavern", "inn", "alehouse", "taproom", "common room", "pub", "hostel", "roadhouse"),
    "temple": ("temple", "shrine", "chapel", "church", "cathedral", "monastery", "abbey", "sanctuary"),
    "crypt": ("crypt", "tomb", "catacomb", "ossuary", "mausoleum", "graveyard", "cemetery", "barrow"),
    "prison": ("prison", "jail", "gaol", "cell", "stockade", "dungeon cell"),
    "dungeon": ("dungeon", "cellar", "sewer", "vault", "labyrinth", "undercroft"),
    "mine": ("mine", "mineshaft", "shaft", "quarry", "seam"),
    "cave": ("cave", "cavern", "grotto", "underdark", "stalactite"),
    "castle": ("castle", "keep", "fortress", "citadel", "palace", "throne room", "battlement", "manor", "courtyard"),
    "arcane": ("tower", "wizard", "observatory", "library", "academy", "laboratory", "sanctum", "arcane"),
    "ruins": ("ruin", "ruins", "ruined"),
    "swamp": ("swamp", "marsh", "bog", "fen", "mire", "bayou"),
    "desert": ("desert", "dune", "oasis", "wastes", "badlands"),
    "ocean": ("ocean", "sea", "ship", "deck", "boat", "coast", "shore", "beach", "island", "lake", "river"),
    "mountain": ("mountain", "peak", "summit", "pass", "ridge", "cliff", "highland", "foothill", "crag"),
    "forest": ("forest", "wood", "woods", "woodland", "grove", "glade", "clearing", "thicket", "jungle", "orchard"),
    "fire": ("volcano", "volcanic", "lava", "magma", "caldera", "inferno"),
    "night": ("night", "midnight", "moonlit", "campfire", "camp"),
    "city": ("city", "town", "village", "hamlet", "street", "market", "square", "gate", "yard", "shop",
             "bakery", "cooperage", "mill", "harbour", "harbor", "dock", "docks", "wharf", "quay",
             "guildhall", "alley", "district", "smithy", "warehouse", "station"),
}
_STOPWORDS = frozenset({"the", "a", "an", "of", "in", "at", "on", "by", "near", "inside", "outside",
                        "beside", "behind", "under", "above", "and", "to", "s"})
MAX_CUE = 800


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"['‘’`]", "", text)
    text = re.sub(r"[\W_]+", " ", text).strip()
    return re.sub(r"^(?:the|a|an) ", "", text)


def _contains(haystack: str, needle: str) -> bool:
    return bool(needle) and f" {needle} " in f" {haystack} "


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.casefold()).strip("-")


def _key(parts: list[str]) -> str:
    slugs = [_slug(part) for part in parts]
    key = ".".join(slug for slug in slugs if slug)
    unaccented = "".join(c for c in unicodedata.normalize("NFKD", "".join(parts)) if not unicodedata.combining(c))
    lossy = not all(slugs) or any(ord(c) > 127 and c.isalnum() for c in unaccented)
    if lossy or len(key) > 64:
        digest = hashlib.sha1("\0".join(parts).encode("utf-8")).hexdigest()[:12]
        key = key[:51].rstrip("-.") + "-" + digest if key else "scene-" + digest
    return key


def _setting_from(text: str) -> str | None:
    words = _norm(text)
    scores = {setting: sum(_contains(words, keyword) or _contains(words, keyword + "s") for keyword in keywords)
              for setting, keywords in SETTING_KEYWORDS.items()}
    best = max(scores.values())
    return next(setting for setting, score in scores.items() if score == best) if best else None


@dataclass(frozen=True)
class SceneCue:
    setting: str
    mood: str
    scene: SceneContext

    def music_fields(self) -> dict:
        return {"setting": self.setting, "mood": self.mood, "scene": self.scene}


def _match_place(place: str, world: WorldNotes,
                 region: str | None) -> tuple[tuple[PlaceTheme, ...], bool] | None:
    """Return the saved place path that `place` names best, and whether words are left over.

    Paths naming more of their ancestors win, then paths in the current region,
    then deeper paths: "Saltmere mill" is Saltmere's mill, not Brackenford's.
    """
    words = _norm(place)
    best = None
    for path in world.places:
        if _contains(words, _norm(path[-1].name)):
            named = sum(_contains(words, _norm(level.name)) for level in path)
            rank = (named, _norm(path[0].name) == region, len(path), len(path[-1].name))
            if best is None or rank > best[0]:
                best = (rank, path)
    if best is None:
        return None
    path = best[1]
    named = {word for level in path for word in _norm(level.name).split()}
    extra = [word for word in words.split() if word not in named and word not in _STOPWORDS]
    return path, bool(extra)


def _current_theme(current: dict | None) -> tuple[PlaceTheme, ...] | None:
    try:
        parsed = SceneContext.parse({"key": "x", "label": "x", "description": "x", "theme": current["theme"]})
        return parsed.theme or None
    except (KeyError, TypeError, ValueError):
        return None


def derive(cue: str, mood: str | None = None, *, world: WorldNotes = WorldNotes(),
           current: dict | None = None) -> SceneCue:
    """Turn '<place>: <what is happening>' into a complete scene cue.

    The place, the text before the first colon, alone decides the key, the
    saved theme path and the setting, so the same place always resolves the
    same way however the moment is described.
    """
    if not isinstance(cue, str) or not cue.strip() or len(cue) > MAX_CUE or not cue.isprintable():
        raise ValueError(f"--scene needs one line of 1-{MAX_CUE} printable characters")
    if mood is not None and mood not in MOODS:
        raise ValueError("invalid scene mood")
    cue = cue.strip()
    place, _, doing = cue.partition(":")
    place = place.strip()
    if not _norm(place):
        raise ValueError("--scene must start with the place, as '<place>: <what is happening>'")
    label = place if len(place) <= 120 else place[:119].rsplit(" ", 1)[0] + "…"

    current_theme = _current_theme(current)
    match = _match_place(place, world, _norm(current_theme[0].name) if current_theme else None)
    if match and not match[1]:
        theme = match[0]
        key = _key([_norm(level.name) for level in theme])
    else:
        key = _key([_norm(place)])
        parent = match[0] if match else (current_theme or ())[:1]
        theme = (parent + (PlaceTheme(label[:100].rstrip()),))[:8] if parent else None

    current_setting = (current or {}).get("setting")
    setting = (_setting_from(place) or _setting_from(" ".join(level.name for level in reversed(theme or ()))) or
               _setting_from(doing) or (current_setting if current_setting in SETTINGS else None) or "city")
    current_era = (current or {}).get("era")
    era = world.era or (current_era.strip() if isinstance(current_era, str) and current_era.strip() and
                        len(current_era) <= 100 and current_era.isprintable() else None)
    resolved = mood or theme_mood(theme) or SCENE_MUSIC[setting][1]
    return SceneCue(setting, resolved, SceneContext(key, label, cue, theme, era))


def is_continuation(cue: SceneCue, current: dict | None, explicit_mood: str | None) -> bool:
    """True when the cue names the scene already on display, so nothing should change."""
    if not current or current.get("key") != cue.scene.key:
        return False
    if explicit_mood is not None and explicit_mood != current.get("mood"):
        return False
    return cue.scene.era is None or cue.scene.era == current.get("era")
