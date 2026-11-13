from __future__ import annotations

import json
import os
import queue
import re
import secrets
import signal
import struct
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from music import MOODS, SETTINGS, SceneContext, MOOD_PROFILES

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


@dataclass(frozen=True)
class SceneJob:
    scene_id: str
    setting: str
    mood: str
    path: Path
    scene: SceneContext | None = None


@dataclass(frozen=True)
class LocalConfig:
    python: Path
    runner: Path
    model: Path
    reference: Path | None = None
    guard: Path | None = None
    width: int = 1024
    height: int = 256
    steps: int = 4
    timeout: int = 900
    density_map: bool = False


def runtime_config(config_path: Path | None = None) -> LocalConfig | None:
    try:
        saved = json.loads(config_path.read_text(encoding="utf-8")) if config_path else {}
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}

    def value(name: str, default: str = "") -> str:
        setting = os.environ.get(name, saved.get(name, default))
        return setting.strip() if isinstance(setting, str) else default

    values = [value(name) for name in
              ("DND_IMAGE_PYTHON", "DND_IMAGE_RUNNER", "DND_IMAGE_MODEL")]
    if not all(values):
        return None
    python = Path(values[0]).expanduser().absolute()
    runner, model = [Path(value).expanduser().resolve() for value in values[1:]]
    density_map = not value("DND_IMAGE_REFERENCE")
    reference = (Path(value("DND_IMAGE_REFERENCE")).expanduser().resolve() if not density_map
                 else Path(__file__).parent / "icons" / "background-density.png")
    guard = Path(value("DND_IMAGE_GUARD")).expanduser().resolve() if value("DND_IMAGE_GUARD") else None
    if not python.is_file() or not os.access(python, os.X_OK) or not runner.is_file() or not model.is_dir():
        return None
    if reference and not reference.is_file() or guard and not guard.is_file():
        return None
    try:
        width = int(value("DND_IMAGE_WIDTH", "1024"))
        height = int(value("DND_IMAGE_HEIGHT", "256"))
        steps = int(value("DND_IMAGE_STEPS", "4"))
        timeout = int(value("DND_IMAGE_TIMEOUT", "900"))
    except ValueError:
        return None
    if not (256 <= width <= 2048 and 256 <= height <= 2048 and
            width % 16 == height % 16 == 0 and 1 <= steps <= 20 and 10 <= timeout <= 1800):
        return None
    return LocalConfig(python, runner, model, reference, guard, width, height, steps, timeout, density_map)


ERA_GUIDANCE = {
    "medieval": "A pre-industrial medieval fantasy world, historically inspired rather than contemporary. Period material culture throughout: rough-hewn timber, limewashed stone, hand-forged iron, pottery, wooden furnishings, beeswax candles and hearth light. ",
    "renaissance": "A Renaissance-inspired fantasy world with carved stone and timber architecture, period furnishings, oil lamps and candles. ",
    "ancient": "An ancient-world fantasy setting with era-appropriate stone, plaster and timber architecture, pottery and oil lamps. ",
    "modern": "An explicitly modern world with contemporary architecture, furnishings, technology appropriate to the described scene. ",
    "futuristic": "An explicitly futuristic world with advanced architecture, technology, materials appropriate to the described scene. ",
}


def prompt_for(setting: str, mood: str, reference: bool = False, density_map: bool = False,
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
    era = scene.era if scene and scene.era else "medieval"
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


def valid_png(path: Path) -> bool:
    try:
        with path.open("rb") as image:
            header = image.read(24)
        return (len(header) == 24 and header[:16] == b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" and
                all(0 < size <= 4096 for size in struct.unpack(">II", header[16:24])) and
                path.stat().st_size > 32)
    except OSError:
        return False


def render_local(job: SceneJob, config: LocalConfig) -> None:
    with tempfile.TemporaryDirectory(prefix="render-", dir=job.path.parent) as temporary:
        directory = Path(temporary)
        prompt = directory / "prompt.txt"
        prompt.write_text(prompt_for(job.setting, job.mood, config.reference is not None, config.density_map,
                                     job.scene), encoding="utf-8")
        command = [str(config.python), str(config.runner), "--model", str(config.model),
                   "--output", str(directory), "--prompt-file", str(prompt),
                   "--width", str(config.width), "--height", str(config.height),
                   "--steps", str(config.steps), "--seed", str(secrets.randbelow(2**31))]
        if config.reference:
            command.extend(["--source", str(config.reference)])
        if config.guard:
            command = [str(config.python), str(config.guard), "--record", str(directory / "watchdog.json"),
                       "--rss-gib", "28", "--swap-growth-gib", "4", "--seconds", str(config.timeout),
                       "--"] + command
        env = os.environ.copy()
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        process = subprocess.Popen(command, cwd=config.runner.parent, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=(os.name == "posix"))
        try:
            code = process.wait(timeout=config.timeout + (30 if config.guard else 0))
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
            raise
        if code:
            raise subprocess.CalledProcessError(code, command)
        output = directory / ("edit.png" if config.reference else "source.png")
        if not valid_png(output):
            raise RuntimeError("local renderer did not produce a PNG")
        output.replace(job.path)


class BackgroundDirector:
    def __init__(self, directory: Path, renderer: Callable[[SceneJob], None] | None = None,
                 render_lock=None, autostart: bool = True):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.renderer = renderer
        self.render_lock = render_lock or threading.Lock()
        self.lock = threading.Lock()
        self.jobs: queue.Queue[SceneJob] = queue.Queue(maxsize=1)
        self.broadcast: Callable[[dict], None] | None = None
        self.revision = 0
        self.music_revision = -1
        self.music_scene_revision = -1
        self.scene_id: str | None = None
        self.setting: str | None = None
        self.scene: SceneContext | None = None
        self.mood = "ominous"
        self.status = "idle" if renderer else "unavailable"
        self.error: str | None = None
        self._load()
        if renderer and autostart:
            threading.Thread(target=self._run, daemon=True, name="dnd-background").start()

    def _load(self) -> None:
        try:
            data = json.loads((self.directory / "current.json").read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            revision = data.get("revision", 0)
            if isinstance(revision, int) and revision >= 0:
                self.revision = revision
            scene_id = data.get("scene_id")
            if not isinstance(scene_id, str) or not re.fullmatch(r"[0-9a-f]{32}", scene_id):
                return
            if data.get("setting") not in SETTINGS or data.get("mood") not in MOODS:
                return
            scene = SceneContext.parse(data.get("scene"))
            self.scene_id, self.setting, self.mood = scene_id, data["setting"], data["mood"]
            self.scene = scene
            self.status = "ready" if valid_png(self.directory / f"{scene_id}.png") else (
                "waiting_music" if self.renderer else "unavailable")
        except (OSError, ValueError):
            pass

    def _snapshot_unlocked(self) -> dict:
        return {"revision": self.revision, "scene_id": self.scene_id, "setting": self.setting,
                "scene": self.scene.to_dict() if self.scene else None, "mood": self.mood,
                "status": self.status, "available": self.renderer is not None,
                "image_url": f"/backgrounds/{self.scene_id}" if self.status == "ready" else None,
                "error": self.error}

    def snapshot(self) -> dict:
        with self.lock:
            return self._snapshot_unlocked()

    def _persist_unlocked(self) -> None:
        data = self._snapshot_unlocked()
        temporary = self.directory / "current.json.tmp"
        try:
            temporary.write_text(json.dumps(data), encoding="utf-8")
            temporary.replace(self.directory / "current.json")
        except OSError:
            temporary.unlink(missing_ok=True)

    def _publish(self, state: dict) -> None:
        if self.broadcast:
            self.broadcast({"background": state})

    def observe_music(self, state: dict) -> None:
        music_revision = state.get("revision", -1)
        scene_revision = state.get("scene_revision", music_revision)
        scene_id, setting = state.get("scene_id"), state.get("setting")
        with self.lock:
            if (scene_revision, music_revision) < (self.music_scene_revision, self.music_revision):
                return
            self.music_revision = music_revision
            self.music_scene_revision = scene_revision
            if scene_id is None:
                if self.scene_id is None:
                    return
                self.scene_id = self.setting = None
                self.scene = None
                self.status = "idle" if self.renderer else "unavailable"
                self.error = None
            elif (not isinstance(scene_id, str) or not re.fullmatch(r"[0-9a-f]{32}", scene_id)
                  or setting not in SETTINGS):
                return
            else:
                if scene_id != self.scene_id:
                    self.scene = SceneContext.parse(state.get("scene"))
                    self.scene_id, self.setting = scene_id, setting
                    self.mood = state.get("mood") if state.get("mood") in MOODS else "ominous"
                    self.status = "waiting_music" if self.renderer else "unavailable"
                    self.error = None
                elif self.status != "waiting_music":
                    return
                if self.status == "waiting_music" and state.get("scene_ready") is True:
                    self.status = "queued"
                    job = SceneJob(scene_id, setting, self.mood, self.directory / f"{scene_id}.png", self.scene)
                    try:
                        self.jobs.get_nowait()
                    except queue.Empty:
                        pass
                    self.jobs.put_nowait(job)
            self.revision += 1
            self._persist_unlocked()
            update = self._snapshot_unlocked()
        self._publish(update)

    def file_for(self, scene_id: str) -> Path | None:
        with self.lock:
            if scene_id != self.scene_id or self.status != "ready":
                return None
            path = self.directory / f"{self.scene_id}.png"
            return path if valid_png(path) else None

    def process_next(self) -> bool:
        try:
            job = self.jobs.get_nowait()
        except queue.Empty:
            return False
        self._process(job)
        return True

    def _process(self, job: SceneJob) -> None:
        with self.render_lock:
            with self.lock:
                if self.scene_id != job.scene_id or self.status != "queued":
                    return
                self.status = "rendering"
                self.revision += 1
                update = self._snapshot_unlocked()
            self._publish(update)
            error = None
            try:
                self.renderer(job)
                if not valid_png(job.path):
                    raise RuntimeError("local renderer did not produce a PNG")
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                error = f"Background generation failed ({type(exc).__name__})"
            with self.lock:
                if self.scene_id != job.scene_id:
                    job.path.unlink(missing_ok=True)
                    return
                if error:
                    job.path.unlink(missing_ok=True)
                self.status = "failed" if error else "ready"
                self.error = error
                self.revision += 1
                self._persist_unlocked()
                update = self._snapshot_unlocked()
        self._publish(update)
        if not error:
            owned = sorted((path for path in self.directory.glob("*.png")
                            if re.fullmatch(r"[0-9a-f]{32}\.png", path.name)),
                           key=lambda path: path.stat().st_mtime, reverse=True)
            for path in owned[3:]:
                if path != job.path:
                    path.unlink(missing_ok=True)

    def _run(self) -> None:
        while True:
            self._process(self.jobs.get())


if __name__ == "__main__":
    import argparse
    from runtime_paths import rt

    parser = argparse.ArgumentParser(description="Configure the display's local background renderer.")
    parser.add_argument("--python", required=True)
    parser.add_argument("--runner", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--guard")
    args = parser.parse_args()
    settings = {"DND_IMAGE_" + name.upper(): setting for name, setting in vars(args).items() if setting}
    os.environ.update(settings)
    if runtime_config() is None:
        parser.error("Renderer paths are not usable; no settings were saved.")
    target = Path(rt("image-config.json"))
    temporary = target.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    print("Local backgrounds configured. Restart the display to use them.")
