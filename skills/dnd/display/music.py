"""Scene music state and one-at-a-time Stable Audio 3 rendering.

The display owns transitions. Render jobs may finish late, but only the current
entry can be advertised or served. The DM supplies a small cue, never a shell
command or a model prompt.
"""

from __future__ import annotations

import json
import os
import queue
import re
import secrets
import subprocess
import threading
import uuid
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from music_filter import lowpass_wav

SETTINGS = frozenset({
    "arcane", "castle", "cave", "city", "crypt", "desert", "dungeon",
    "fire", "forest", "mine", "mountain", "night", "ocean", "prison",
    "ruins", "swamp", "tavern", "temple",
})
SETTING_ALIASES = {"tower": "arcane"}
MOODS = frozenset({"calm", "curious", "ominous", "tense", "warm"})


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
        return "Place ancestry: " + " > ".join(level.name for level in self.theme) + ". The resolved atmosphere below takes precedence over ancestor tones. "

@dataclass(frozen=True)
class Job:
    revision: int
    entry: str
    kind: str
    prompt: str
    seconds: int
    seed: int
    cfg: float
    path: Path


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
NEGATIVE_PROMPT = "Vocals, distortion, guitar, lute, drone."
BATTLE_PROMPT = (
    "Medieval fantasy battle music with frame drum. Driving rhythm, "
    "forceful bowed string phrases. Urgent and determined."
)


def prompt_for(setting: str, mood: str, kind: str, scene: SceneContext | None = None) -> str:
    era = scene.era if scene and scene.era else "medieval"
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
            context += " Place ancestry: " + " > ".join(level.name for level in scene.theme) + "."
    return f"{prefix} {purpose}{context}".strip()


def runtime_config(config_path: Path) -> tuple[Path | None, Path | None]:
    try:
        saved = json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    cli_value = os.environ.get("DND_MUSIC_CLI") or saved.get("cli")
    hf_value = os.environ.get("DND_MUSIC_HF_HOME") or saved.get("hf_home")
    cli = Path(cli_value).expanduser() if cli_value else None
    hf_home = Path(hf_value).expanduser() if hf_value else None
    if not cli or not cli.is_file() or not os.access(cli, os.X_OK):
        return None, hf_home
    return cli.resolve(), hf_home


def render_cli(job: Job, cli: Path, hf_home: Path | None) -> None:
    temp_path = job.path.with_suffix(".partial.wav")
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    if hf_home:
        env["HF_HOME"] = str(hf_home)
    cmd = [
        str(cli), "--dit", "medium", "--decoder", "same-l",
        "--seconds", str(job.seconds), "--steps", "8",
        "--seed", str(job.seed), "--cfg", str(job.cfg),
        "--prompt", job.prompt, "--out", str(temp_path),
    ]
    cmd.extend(["--negative-prompt", NEGATIVE_PROMPT])
    filtered_path = job.path.with_suffix(".filtered.wav")
    try:
        subprocess.run(cmd, cwd=cli.parent, env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       timeout=120)
        if not temp_path.is_file() or temp_path.stat().st_size < 44:
            raise RuntimeError("renderer did not write a WAV")
        lowpass_wav(temp_path, filtered_path)
        filtered_path.replace(job.path)
    finally:
        temp_path.unlink(missing_ok=True)
        filtered_path.unlink(missing_ok=True)


class MusicDirector:
    """One authoritative scene entry and one serial render queue."""

    def __init__(self, directory: Path, cli: Path | None, hf_home: Path | None,
                 renderer: Callable[[Job], None] | None = None,
                 autostart: bool = True, render_lock=None):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cli = cli
        self.hf_home = hf_home
        self.available = renderer is not None or cli is not None
        self.renderer = renderer or (lambda job: render_cli(job, cli, hf_home))
        self.broadcast: Callable[[dict], None] | None = None
        self.lock = threading.Lock()
        self.render_lock = render_lock or threading.Lock()
        self.jobs: queue.Queue[Job] = queue.Queue()
        self.revision = 0
        self.mode = "idle"
        self.setting: str | None = None
        self.scene: SceneContext | None = None
        self.mood = "ominous"
        self.entry: str | None = None
        self.ready: dict[str, Path] = {}
        self.expected: tuple[str, ...] = ()
        self.error: str | None = None
        self.scene_id: str | None = None
        self.scene_revision = 0
        self.scene_ready = False
        self.seen: set[str] = set()
        self.seen_order: deque[str] = deque()
        self._load_state()
        if autostart and self.available:
            threading.Thread(target=self._run, daemon=True, name="dnd-music").start()

    def _load_state(self) -> None:
        try:
            data = json.loads((self.directory / "current.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        revision = data.get("revision", 0)
        if not isinstance(revision, int) or revision < 0:
            return
        self.revision = revision
        scene_revision = data.get("scene_revision", revision)
        if isinstance(scene_revision, int) and scene_revision >= 0:
            self.scene_revision = scene_revision
        if data.get("mode") == "idle":
            return
        entry = data.get("entry")
        if not isinstance(entry, str) or len(entry) != 32 or any(
                c not in "0123456789abcdef" for c in entry):
            return
        mode = data.get("mode")
        setting = data.get("setting")
        if mode not in {"explore", "combat"} or (setting not in SETTINGS and
                not (mode == "combat" and setting is None)):
            return
        try:
            scene = SceneContext.parse(data.get("scene"))
        except ValueError:
            return
        self.mode = mode
        self.setting = setting
        self.scene = scene
        mood = data.get("mood", "ominous")
        self.mood = mood if mood in MOODS else "ominous"
        self.entry = entry
        scene_id = data.get("scene_id", entry if setting and mode == "explore" else None)
        self.scene_id = scene_id if isinstance(scene_id, str) and re.fullmatch(r"[0-9a-f]{32}", scene_id) else None
        self.scene_ready = data.get("scene_ready") is True
        self.ready = {}
        expected = data.get("expected", [])
        if not isinstance(expected, list):
            expected = []
        self.expected = tuple(kind for kind in expected if kind in {"intro", "bed", "combat"})
        if "scene_id" not in data and mode == "explore":
            self.expected = ("intro", "bed")
        for kind in self.expected:
            path = self.directory / f"{entry}-{kind}.wav"
            if path.is_file():
                self.ready[kind] = path
            elif self.available:
                self.jobs.put(self._make_job(kind))
        if set(self.ready) >= {"intro", "bed"}:
            self.scene_ready = True
        seen = data.get("seen", [])
        self.seen_order = deque(e for e in seen[-512:] if isinstance(e, str)) if isinstance(seen, list) else deque()
        self.seen = set(self.seen_order)

    def _persist_unlocked(self) -> None:
        data = {
            "revision": self.revision, "mode": self.mode,
            "setting": self.setting, "mood": self.mood,
            "scene": self.scene.to_dict() if self.scene else None,
            "entry": self.entry, "expected": list(self.expected),
            "scene_id": self.scene_id, "scene_ready": self.scene_ready,
            "scene_revision": self.scene_revision,
            "seen": list(self.seen_order),
        }
        target = self.directory / "current.json"
        temporary = self.directory / "current.json.tmp"
        try:
            temporary.write_text(json.dumps(data), encoding="utf-8")
            temporary.replace(target)
        except OSError:
            temporary.unlink(missing_ok=True)

    def _make_job(self, kind: str) -> Job:
        assert self.entry is not None
        seconds = 120 if kind == "bed" else 10
        return Job(self.revision, self.entry, kind,
                   prompt_for(self.setting or "cave", self.mood, kind, self.scene),
                   seconds, secrets.randbelow(2**31),
                   2.0,
                   self.directory / f"{self.entry}-{kind}.wav")

    def set_broadcast(self, callback: Callable[[dict], None]) -> None:
        self.broadcast = callback

    def _snapshot_unlocked(self) -> dict:
        entry = self.entry
        return {
            "revision": self.revision,
            "mode": self.mode,
            "setting": self.setting,
            "scene": self.scene.to_dict() if self.scene else None,
            "entry": entry,
            "scene_id": self.scene_id,
            "scene_revision": self.scene_revision,
            "scene_ready": self.scene_ready,
            "mood": self.mood,
            "available": self.available,
            "intro_url": f"/audio/music/{entry}/intro" if "intro" in self.ready else None,
            "bed_url": f"/audio/music/{entry}/bed" if "bed" in self.ready else None,
            "combat_url": f"/audio/music/{entry}/combat" if "combat" in self.ready else None,
            "error": self.error,
        }

    def snapshot(self) -> dict:
        with self.lock:
            return self._snapshot_unlocked()

    def _publish(self, state: dict) -> None:
        if self.broadcast:
            self.broadcast({"music": state})

    def _remember(self, event_id: str) -> bool:
        if event_id in self.seen:
            return False
        self.seen.add(event_id)
        self.seen_order.append(event_id)
        if len(self.seen_order) > 512:
            self.seen.remove(self.seen_order.popleft())
        return True

    def transition(self, event: str, event_id: str, setting: str | None = None,
                   mood: str | None = None, scene: dict | SceneContext | None = None) -> dict:
        if event == "scene":
            setting = SETTING_ALIASES.get(setting, setting)
        if event not in {"scene", "combat_start", "combat_end"}:
            raise ValueError("invalid music event")
        if event == "scene" and setting not in SETTINGS:
            raise ValueError("invalid music setting")
        if mood is not None and (not isinstance(mood, str) or mood not in MOODS):
            raise ValueError("invalid music mood")
        if not isinstance(event_id, str) or len(event_id) > 64 or not event_id:
            raise ValueError("invalid music event id")
        context = SceneContext.parse(scene)
        if event != "scene" and context is not None:
            raise ValueError("scene context requires a scene event")
        with self.lock:
            if event == "scene":
                if context is not None and context.theme is None and self.scene is not None:
                    context = replace(context, theme=self.scene.theme)
                if context is not None and context.era is None and self.scene is not None:
                    context = replace(context, era=self.scene.era)
                inherited = next((level.mood for level in reversed(context.theme or ()) if level.mood is not None), None) if context else None
                same_place = (context is not None and self.scene is not None and
                              context.key == self.scene.key and setting == self.setting and
                              context.theme == self.scene.theme)
                resolved_mood = (self.mood if mood is None and same_place else
                                 mood or inherited or "ominous")
            else:
                resolved_mood = mood or self.mood
            if not self._remember(event_id):
                return self._snapshot_unlocked()
            if (event == "scene" and context is not None and self.scene is not None and
                    context.key == self.scene.key and setting == self.setting and
                    context.theme == self.scene.theme and context.era == self.scene.era and
                    resolved_mood == self.mood):
                return self._snapshot_unlocked()
            if event == "combat_start" and self.mode == "combat":
                return self._snapshot_unlocked()
            if event == "combat_end" and self.mode != "combat":
                return self._snapshot_unlocked()
            if event == "scene":
                self.scene = context
                self.scene_id = uuid.uuid4().hex
                self.scene_revision += 1
                self.scene_ready = False
            if event == "scene" and self.mode == "combat":
                self.setting = setting
                self.mood = resolved_mood
                self._persist_unlocked()
                state = self._snapshot_unlocked()
            else:
                if event == "combat_start":
                    self.mode = "combat"
                    kinds = ("combat",)
                elif event == "combat_end":
                    self.mode = "explore" if self.setting else "idle"
                    if self.setting and self.scene_id is None:
                        self.scene_id = uuid.uuid4().hex
                        self.scene_revision += 1
                        self.scene_ready = False
                    kinds = (("bed",) if self.scene_ready else ("intro", "bed")) if self.setting else ()
                else:
                    self.mode = "explore"
                    self.setting = setting
                    self.mood = resolved_mood
                    kinds = ("intro", "bed")
                self.revision += 1
                self.entry = uuid.uuid4().hex
                self.ready = {}
                self.expected = kinds
                self.error = None
                if self.available:
                    for kind in kinds:
                        self.jobs.put(self._make_job(kind))
                self._persist_unlocked()
                state = self._snapshot_unlocked()
        self._publish(state)
        return state

    def reset(self) -> None:
        with self.lock:
            self.revision += 1
            self.mode = "idle"
            self.setting = None
            self.scene = None
            self.entry = None
            self.ready = {}
            self.expected = ()
            self.error = None
            self.scene_id = None
            self.scene_revision += 1
            self.scene_ready = False
            self.seen.clear()
            self.seen_order.clear()
            self._persist_unlocked()
            state = self._snapshot_unlocked()
        self._publish(state)

    def file_for(self, entry: str, kind: str) -> Path | None:
        with self.lock:
            if entry != self.entry or kind not in {"intro", "bed", "combat"}:
                return None
            path = self.ready.get(kind)
            return path if path and path.is_file() else None

    def _prune_old(self) -> None:
        files = sorted((p for p in self.directory.glob("*.wav")
                        if re.fullmatch(r"[0-9a-f]{32}-(intro|bed|combat)\.wav", p.name)),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        with self.lock:
            current = self.entry
        keep = {current} if current else set()
        for path in files:
            entry = path.name.split("-", 1)[0]
            if len(keep) < 3:
                keep.add(entry)
            elif entry not in keep:
                path.unlink(missing_ok=True)

    def process_next(self) -> bool:
        try:
            job = self.jobs.get_nowait()
        except queue.Empty:
            return False
        self._process(job)
        return True

    def _process(self, job: Job) -> None:
        with self.lock:
            current = job.revision == self.revision
        if not current:
            return
        try:
            with self.render_lock:
                with self.lock:
                    if job.revision != self.revision:
                        return
                self.renderer(job)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
                RuntimeError) as exc:
            with self.lock:
                if job.revision == self.revision:
                    self.error = f"{job.kind} generation failed: {type(exc).__name__}"
                    self._persist_unlocked()
                    state = self._snapshot_unlocked()
                else:
                    state = None
            if state:
                self._publish(state)
            return
        with self.lock:
            if job.revision == self.revision and job.path.is_file():
                self.ready[job.kind] = job.path
                if set(self.ready) >= {"intro", "bed"}:
                    self.scene_ready = True
                self._persist_unlocked()
                state = self._snapshot_unlocked()
            else:
                state = None
        if state:
            self._publish(state)
            self._prune_old()
        else:
            job.path.unlink(missing_ok=True)

    def _run(self) -> None:
        while True:
            job = self.jobs.get()
            self._process(job)
