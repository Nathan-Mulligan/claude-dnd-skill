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
import wave
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from music_filter import lowpass_wav
from scene_engine import (  # noqa: F401  (re-exported for callers of music)
    MOOD_PROFILES, MOODS, SETTING_ALIASES, SETTINGS, SceneContext, music_prompt as prompt_for, theme_mood,
)


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


NEGATIVE_PROMPT = "Vocals, distortion, guitar, lute, drone."


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
        try:
            lowpass_wav(temp_path, filtered_path)
        except (wave.Error, EOFError, ValueError, ImportError) as exc:
            raise RuntimeError(f"music filtering failed: {type(exc).__name__}") from exc
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
                inherited = theme_mood(context.theme) if context else None
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
