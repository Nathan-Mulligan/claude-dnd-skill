"""Scene music state and one-at-a-time Stable Audio 3 rendering.

The display owns transitions. Render jobs may finish late, but only the current
entry can be advertised or served. The DM supplies a small cue, never a shell
command or a model prompt.
"""

from __future__ import annotations

import json
import os
import queue
import secrets
import subprocess
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SETTINGS = frozenset({
    "arcane", "castle", "cave", "city", "crypt", "desert", "dungeon",
    "fire", "forest", "mine", "mountain", "night", "ocean", "prison",
    "ruins", "swamp", "tavern", "temple",
})
MOODS = frozenset({"calm", "curious", "ominous", "tense", "warm"})

INSTRUMENTS = {
    "arcane": "bowed strings, hand bells and hollow wooden flute",
    "castle": "natural horns, bowed strings and plucked harp",
    "cave": "low bowed strings, hollow wooden flute and distant hand bells",
    "city": "fiddle, lute and soft hand drum",
    "crypt": "low cello, distant hand bells and wooden flute",
    "desert": "wooden flute, hand drum and sparse plucked strings",
    "dungeon": "low bowed strings, hand bells and hollow wooden flute",
    "fire": "bowed low strings, natural horns and quiet hand drum",
    "forest": "wooden flute, plucked harp and gentle bowed strings",
    "mine": "low cello, distant hand bells and sparse hand drum",
    "mountain": "natural horns, bowed strings and airy wooden flute",
    "night": "soft bowed strings, wooden flute and sparse harp",
    "ocean": "bowed strings, wooden flute and low hand drum",
    "prison": "haunting cello, distant hand bells and low bowed strings",
    "ruins": "low bowed strings, plucked harp and wooden flute",
    "swamp": "low cello, hollow wooden flute and hand bells",
    "tavern": "fiddle, plucked lute, warm bowed bass and hand drum",
    "temple": "bowed strings, hand bells and wooden flute",
}


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


def prompt_for(setting: str, mood: str, kind: str) -> str:
    instruments = INSTRUMENTS[setting]
    if kind == "intro":
        return (
            f"A ten-second instrumental dark fantasy {setting} scene introduction. "
            f"A memorable melody played by {instruments}. {mood.capitalize()} atmosphere. "
            "One coherent musical phrase that grows and resolves on a clear final chord. "
            "No vocals, no speech, no sound effects."
        )
    if kind == "combat":
        return (
            "A ten-second instrumental fantasy battle scene introduction. "
            "A bold natural brass horn melody answered by urgent bowed strings "
            "over driving skin drums. One coherent musical phrase with a decisive "
            "final hit. No vocals, no speech, no sound effects."
        )
    return (
        f"Two minutes of sparse dark fantasy {setting} background ambience. "
        f"Quiet {instruments}, with long near-still stretches between brief gestures. "
        f"{mood.capitalize()} and spacious, unobtrusive under narration. "
        "Slow variation, minimal notes, no strong beat or climax. Soft airy pads "
        "may sit far behind the acoustic instruments. Gentle start and end for "
        "background repeat. No guitar, vocals or speech."
    )


def runtime_config(config_path: Path) -> tuple[Path | None, Path | None]:
    try:
        saved = json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
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
    if job.kind == "bed":
        cmd.extend(["--negative-prompt", "busy melody, frequent notes, "
                    "constant arpeggios, drums, battle music, guitar, "
                    "electric instruments, modern pop, vocals, speech"])
    try:
        subprocess.run(cmd, cwd=cli.parent, env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       timeout=120)
        if not temp_path.is_file() or temp_path.stat().st_size < 44:
            raise RuntimeError("renderer did not write a WAV")
        temp_path.replace(job.path)
    finally:
        temp_path.unlink(missing_ok=True)


class MusicDirector:
    """One authoritative scene entry and one serial render queue."""

    def __init__(self, directory: Path, cli: Path | None, hf_home: Path | None,
                 renderer: Callable[[Job], None] | None = None,
                 autostart: bool = True):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cli = cli
        self.hf_home = hf_home
        self.available = renderer is not None or cli is not None
        self.renderer = renderer or (lambda job: render_cli(job, cli, hf_home))
        self.broadcast: Callable[[dict], None] | None = None
        self.lock = threading.Lock()
        self.jobs: queue.Queue[Job] = queue.Queue()
        self.revision = 0
        self.mode = "idle"
        self.setting: str | None = None
        self.mood = "ominous"
        self.entry: str | None = None
        self.ready: dict[str, Path] = {}
        self.error: str | None = None
        self.seen: set[str] = set()
        self.seen_order: deque[str] = deque()
        if autostart and self.available:
            threading.Thread(target=self._run, daemon=True, name="dnd-music").start()

    def set_broadcast(self, callback: Callable[[dict], None]) -> None:
        self.broadcast = callback

    def _snapshot_unlocked(self) -> dict:
        entry = self.entry
        return {
            "revision": self.revision,
            "mode": self.mode,
            "setting": self.setting,
            "entry": entry,
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
                   mood: str = "ominous") -> dict:
        if event not in {"scene", "combat_start", "combat_end"}:
            raise ValueError("invalid music event")
        if event == "scene" and setting not in SETTINGS:
            raise ValueError("invalid music setting")
        if mood not in MOODS:
            raise ValueError("invalid music mood")
        if not isinstance(event_id, str) or len(event_id) > 64 or not event_id:
            raise ValueError("invalid music event id")
        with self.lock:
            if not self._remember(event_id):
                return self._snapshot_unlocked()
            if event == "combat_start" and self.mode == "combat":
                return self._snapshot_unlocked()
            if event == "combat_end" and self.mode != "combat":
                return self._snapshot_unlocked()
            if event == "combat_start":
                self.mode = "combat"
                kinds = ("combat",)
            elif event == "combat_end":
                self.mode = "explore" if self.setting else "idle"
                kinds = ("bed",) if self.setting else ()
            else:
                self.mode = "explore"
                self.setting = setting
                self.mood = mood
                kinds = ("intro", "bed")
            self.revision += 1
            self.entry = uuid.uuid4().hex
            self.ready = {}
            self.error = None
            for kind in kinds:
                seconds = 120 if kind == "bed" else 10
                job = Job(self.revision, self.entry, kind,
                          prompt_for(self.setting or "cave", self.mood, kind),
                          seconds, secrets.randbelow(2**31),
                          3.0 if kind == "bed" else 1.0,
                          self.directory / f"{self.entry}-{kind}.wav")
                if self.available:
                    self.jobs.put(job)
            state = self._snapshot_unlocked()
        self._publish(state)
        return state

    def reset(self) -> None:
        with self.lock:
            self.revision += 1
            self.mode = "idle"
            self.setting = None
            self.entry = None
            self.ready = {}
            self.error = None
            self.seen.clear()
            self.seen_order.clear()
            state = self._snapshot_unlocked()
        self._publish(state)

    def file_for(self, entry: str, kind: str) -> Path | None:
        with self.lock:
            if entry != self.entry or kind not in {"intro", "bed", "combat"}:
                return None
            path = self.ready.get(kind)
            return path if path and path.is_file() else None

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
            self.renderer(job)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
                RuntimeError) as exc:
            with self.lock:
                if job.revision == self.revision:
                    self.error = f"{job.kind} generation failed: {type(exc).__name__}"
                    state = self._snapshot_unlocked()
                else:
                    state = None
            if state:
                self._publish(state)
            return
        with self.lock:
            if job.revision == self.revision and job.path.is_file():
                self.ready[job.kind] = job.path
                state = self._snapshot_unlocked()
            else:
                state = None
        if state:
            self._publish(state)
        else:
            job.path.unlink(missing_ok=True)

    def _run(self) -> None:
        while True:
            job = self.jobs.get()
            self._process(job)
