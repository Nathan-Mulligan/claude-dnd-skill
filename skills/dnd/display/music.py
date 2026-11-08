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


INTRO_PROMPTS = {
    "forest": "A ten-second instrumental fantasy forest scene introduction. A memorable rising wood flute melody answered by plucked harp, gentle strings and light hand percussion. Curious woodland exploration, bright but mysterious. One coherent musical phrase that grows and resolves on a clear final chord. No vocals, no speech, no sound effects.",
    "tavern": "A ten-second instrumental fantasy tavern scene introduction. A catchy fiddle melody answered by lute over hand drum and warm bass. A bustling roadside inn, inviting and slightly mischievous. One coherent folk dance phrase that grows and resolves on a clear final chord. No vocals, no speech, no sound effects.",
    "prison": "A ten-second instrumental dark fantasy prison dungeon scene introduction. A haunting, memorable cello melody answered by a distant bell over low strings and a restrained pulse. Captives behind iron bars deep underground. One coherent minor-key phrase that develops and resolves softly. No vocals, no speech, no sound effects.",
    "cave": "A ten-second dark fantasy instrumental scene-opening cue for adventurers entering a cave occupied by cultists and goblins. A memorable four-note melody in D minor played by low strings, answered by a wooden flute; an ominous brass swell and restrained frame drum build to a clear final cadence. Coherent musical phrase with a beginning, development, and resolved ending. Cinematic orchestral dungeon music, no vocals, no speech, no sound effects.",
}


def prompt_for(setting: str, mood: str, kind: str) -> str:
    instruments = INSTRUMENTS[setting]
    if kind == "intro":
        if setting in INTRO_PROMPTS:
            return INTRO_PROMPTS[setting]
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
        self.mode = mode
        self.setting = setting
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
                   prompt_for(self.setting or "cave", self.mood, kind),
                   seconds, secrets.randbelow(2**31),
                   3.0 if kind == "bed" else 1.0,
                   self.directory / f"{self.entry}-{kind}.wav")

    def set_broadcast(self, callback: Callable[[dict], None]) -> None:
        self.broadcast = callback

    def _snapshot_unlocked(self) -> dict:
        entry = self.entry
        return {
            "revision": self.revision,
            "mode": self.mode,
            "setting": self.setting,
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
            if event == "scene":
                self.scene_id = uuid.uuid4().hex
                self.scene_revision += 1
                self.scene_ready = False
            if event == "scene" and self.mode == "combat":
                self.setting = setting
                self.mood = mood
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
                    self.mood = mood
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
