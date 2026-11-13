from __future__ import annotations

import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
import zlib
from pathlib import Path
from unittest import mock

DISPLAY = Path(__file__).resolve().parent.parent / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))
import backgrounds
import music


def png(path: Path) -> None:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b""))


def audio(job):
    with wave.open(str(job.path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\0\0" * 80)


class BackgroundTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.events = []
        self.images = backgrounds.BackgroundDirector(root / "images", renderer=lambda job: png(job.path), autostart=False)
        self.images.broadcast = self.events.append
        self.music = music.MusicDirector(root / "music", None, None, renderer=audio, autostart=False)
        self.music.set_broadcast(lambda payload: self.images.observe_music(payload["music"]))

    def drain(self):
        while self.music.process_next():
            pass

    def test_waits_for_both_tracks_and_ignores_duplicate_states(self):
        first = self.music.transition("scene", "cave-1", "cave")
        self.assertEqual(self.images.snapshot()["status"], "waiting_music")
        self.assertFalse(self.images.process_next())
        self.music.process_next()
        self.assertFalse(self.images.process_next())
        self.music.process_next()
        self.images.observe_music(self.music.snapshot())
        self.assertTrue(self.images.process_next())
        self.assertFalse(self.images.process_next())
        ready = self.images.snapshot()
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["scene_id"], first["scene_id"])
        self.assertTrue(self.images.file_for(first["scene_id"]))

    def test_audio_failure_cannot_trigger_image(self):
        def fail_intro(job):
            if job.kind == "intro":
                raise RuntimeError("failed")
            audio(job)
        self.music.renderer = fail_intro
        self.music.transition("scene", "cave-1", "cave")
        self.drain()
        self.assertFalse(self.images.process_next())
        self.assertEqual(self.images.snapshot()["status"], "waiting_music")

    def test_combat_preserves_completed_scene_and_art(self):
        first = self.music.transition("scene", "cave-1", "cave")
        self.drain()
        self.images.process_next()
        ready = self.images.snapshot()
        self.music.transition("combat_start", "combat-1")
        self.drain()
        self.music.transition("combat_end", "combat-2")
        self.drain()
        self.assertEqual(self.images.snapshot(), ready)
        self.assertEqual(self.music.snapshot()["scene_id"], first["scene_id"])
        self.assertTrue(self.images.file_for(first["scene_id"]))
        self.assertFalse(self.images.process_next())

    def test_scene_during_combat_gets_new_prerequisite_tracks_on_return(self):
        self.music.transition("scene", "cave-1", "cave")
        self.drain()
        self.images.process_next()
        self.music.transition("combat_start", "combat-1")
        update = self.music.transition("scene", "ocean-1", "ocean")
        self.assertEqual(self.images.snapshot()["setting"], "ocean")
        self.assertFalse(self.images.process_next())
        self.music.transition("combat_end", "combat-2")
        self.drain()
        self.assertTrue(self.music.snapshot()["intro_url"])
        self.assertTrue(self.images.process_next())
        self.assertEqual(self.images.snapshot()["scene_id"], update["scene_id"])

    def test_delayed_music_snapshot_cannot_revert_scene_entered_during_combat(self):
        self.music.transition("scene", "cave-1", "cave")
        old = self.music.transition("combat_start", "combat-1")
        update = self.music.transition("scene", "ocean-1", "ocean")
        self.assertEqual(old["revision"], update["revision"])
        self.images.observe_music(old)
        self.assertEqual(self.images.snapshot()["scene_id"], update["scene_id"])
        self.assertEqual(self.images.snapshot()["setting"], "ocean")

    def test_late_image_after_scene_change_or_clear_is_never_served(self):
        first = self.music.transition("scene", "cave-1", "cave")
        self.drain()
        def interrupted(job):
            png(job.path)
            self.music.transition("scene", "ocean-1", "ocean")
        self.images.renderer = interrupted
        self.images.process_next()
        self.assertIsNone(self.images.file_for(first["scene_id"]))
        self.assertFalse((self.images.directory / f"{first['scene_id']}.png").exists())
        self.assertFalse(any(e["background"]["status"] == "ready" for e in self.events))
        self.drain()
        self.images.renderer = lambda job: (png(job.path), self.music.reset())
        self.images.process_next()
        self.assertEqual(self.images.snapshot()["status"], "idle")

    def test_restart_reuses_art_and_resumes_missing_art_once(self):
        first = self.music.transition("scene", "cave-1", "cave")
        self.drain()
        self.images.process_next()
        restarted = backgrounds.BackgroundDirector(self.images.directory, renderer=lambda job: png(job.path), autostart=False)
        restarted.observe_music(self.music.snapshot())
        self.assertTrue(restarted.file_for(first["scene_id"]))
        self.assertFalse(restarted.process_next())
        restarted.file_for(first["scene_id"]).unlink()
        restarted = backgrounds.BackgroundDirector(self.images.directory, renderer=lambda job: png(job.path), autostart=False)
        restarted.observe_music(self.music.snapshot())
        restarted.observe_music(self.music.snapshot())
        self.assertTrue(restarted.process_next())
        self.assertFalse(restarted.process_next())

    def test_existing_music_state_without_visual_metadata_migrates_only_explore(self):
        self.music.transition("scene", "cave-1", "cave")
        self.drain()
        current = self.music.directory / "current.json"
        data = json.loads(current.read_text(encoding="utf-8"))
        data.pop("scene_id")
        data.pop("scene_ready")
        current.write_text(json.dumps(data), encoding="utf-8")
        restarted = music.MusicDirector(self.music.directory, None, None, renderer=audio, autostart=False)
        self.assertEqual(restarted.snapshot()["scene_id"], data["entry"])
        self.assertTrue(restarted.snapshot()["scene_ready"])
        data["mode"] = "combat"
        data["expected"] = ["combat"]
        current.write_text(json.dumps(data), encoding="utf-8")
        combat = music.MusicDirector(self.music.directory, None, None, renderer=audio, autostart=False)
        self.assertIsNone(combat.snapshot()["scene_id"])
        after = combat.transition("combat_end", "combat-return")
        self.assertTrue(after["scene_id"])
        self.assertFalse(after["scene_ready"])

    def test_legacy_post_combat_save_generates_missing_intro_before_art(self):
        self.music.transition("scene", "cave-1", "cave")
        self.drain()
        self.music.transition("combat_start", "combat-1")
        self.drain()
        self.music.transition("combat_end", "combat-2")
        self.drain()
        current = self.music.directory / "current.json"
        data = json.loads(current.read_text(encoding="utf-8"))
        for key in ("scene_id", "scene_ready", "scene_revision"):
            data.pop(key)
        self.assertEqual(data["expected"], ["bed"])
        current.write_text(json.dumps(data), encoding="utf-8")
        images = backgrounds.BackgroundDirector(self.images.directory / "legacy", renderer=lambda job: png(job.path), autostart=False)
        restarted = music.MusicDirector(self.music.directory, None, None, renderer=audio, autostart=False)
        restarted.set_broadcast(lambda payload: images.observe_music(payload["music"]))
        images.observe_music(restarted.snapshot())
        self.assertEqual(images.snapshot()["status"], "waiting_music")
        self.assertFalse(restarted.snapshot()["scene_ready"])
        self.assertTrue(restarted.snapshot()["bed_url"])
        self.assertIsNone(restarted.snapshot()["intro_url"])
        self.assertFalse(images.process_next())
        self.assertTrue(restarted.process_next())
        self.assertFalse(restarted.process_next())
        self.assertTrue(restarted.snapshot()["scene_ready"])
        self.assertTrue(images.process_next())
        self.assertEqual(images.snapshot()["status"], "ready")
        again = music.MusicDirector(self.music.directory, None, None, renderer=audio, autostart=False)
        images.observe_music(again.snapshot())
        self.assertEqual(again.snapshot()["scene_id"], restarted.snapshot()["scene_id"])
        self.assertTrue(again.snapshot()["scene_ready"])
        self.assertFalse(again.process_next())
        self.assertFalse(images.process_next())

    def test_image_failure_leaves_music_ready_and_does_not_retry(self):
        self.music.transition("scene", "cave-1", "cave")
        self.drain()
        before = self.music.snapshot()
        self.images.renderer = mock.Mock(side_effect=RuntimeError("private error path"))
        self.images.process_next()
        self.images.observe_music(before)
        self.assertEqual(self.images.snapshot()["status"], "failed")
        self.assertNotIn("private error", self.images.snapshot()["error"])
        self.assertFalse(self.images.process_next())
        self.assertEqual(self.music.snapshot(), before)

    def test_image_file_route_requires_current_scene_and_authentication(self):
        first = self.music.transition("scene", "cave-1", "cave")
        self.drain()
        self.images.process_next()
        with mock.patch.dict(os.environ, {"DND_RUNTIME_DIR": str(Path(self.temp.name) / "app")}):
            spec = importlib.util.spec_from_file_location("_background_app_test", DISPLAY / "dnd-display-app.py")
            appmod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(appmod)
            appmod._background = self.images
            appmod._lan_token = "test-token"
            client = appmod.app.test_client()
            url = self.images.snapshot()["image_url"]
            self.assertEqual(client.get(url).status_code, 403)
            response = client.get(url, headers={"X-DND-Token": "test-token"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/png")
            response.close()
            self.music.transition("scene", "ocean-1", "ocean")
            self.assertEqual(client.get(url, headers={"X-DND-Token": "test-token"}).status_code, 404)
            self.assertEqual(client.get("/backgrounds/../../etc/passwd").status_code, 404)

    def test_shared_render_lease_prevents_audio_and_image_overlap(self):
        lease = threading.Lock()
        self.music.render_lock = self.images.render_lock = lease
        self.music.renderer = lambda job: (self.assertTrue(lease.locked()), audio(job))
        self.images.renderer = lambda job: (self.assertTrue(lease.locked()), png(job.path))
        self.music.transition("scene", "cave-1", "cave")
        self.drain()
        self.assertTrue(self.images.process_next())


class LocalConfigTest(unittest.TestCase):
    def test_configured_python_keeps_its_virtual_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / "venv")], check=True)
            python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            runner = root / "runner.py"
            runner.touch()
            env = {"DND_IMAGE_PYTHON": str(python), "DND_IMAGE_RUNNER": str(runner),
                   "DND_IMAGE_MODEL": str(root)}
            with mock.patch.dict(os.environ, env, clear=True):
                config = backgrounds.runtime_config()
            result = subprocess.run([str(config.python), "-c", "import sys; print(sys.prefix)"],
                                    check=True, capture_output=True, text=True)
            self.assertEqual(Path(result.stdout.strip()).resolve(), (root / "venv").resolve())

    def test_default_reference_is_density_map_and_explicit_art_has_distinct_prompt(self):
        with tempfile.TemporaryDirectory() as temporary:
            runner = Path(temporary) / "runner.py"
            runner.touch()
            env = {"DND_IMAGE_PYTHON": sys.executable, "DND_IMAGE_RUNNER": str(runner),
                   "DND_IMAGE_MODEL": temporary}
            with mock.patch.dict(os.environ, env, clear=True):
                config = backgrounds.runtime_config()
                self.assertTrue(config.density_map)
                self.assertTrue(backgrounds.valid_png(config.reference))
                density_prompt = backgrounds.prompt_for("ocean", "calm", True, config.density_map)
                self.assertIn("density map", density_prompt)
                self.assertIn("full-colour", density_prompt)
                os.environ["DND_IMAGE_REFERENCE"] = str(config.reference)
                explicit = backgrounds.runtime_config()
                self.assertFalse(explicit.density_map)
                self.assertIn("Replace its entire environment", backgrounds.prompt_for("ocean", "calm", True, False))
                os.environ["DND_IMAGE_WIDTH"] = "999999"
                self.assertIsNone(backgrounds.runtime_config())

    def test_local_adapter_uses_fresh_directory_offline_mode_and_validates_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = backgrounds.LocalConfig(Path(sys.executable), root / "runner.py", root)
            job = backgrounds.SceneJob("a" * 32, "cave", "ominous", root / "image.png")
            def launch(command, **kwargs):
                directory = Path(command[command.index("--output") + 1])
                self.assertEqual(kwargs["env"]["HF_HUB_OFFLINE"], "1")
                self.assertTrue((directory / "prompt.txt").is_file())
                png(directory / "source.png")
                return mock.Mock(wait=mock.Mock(return_value=0))
            with mock.patch.object(backgrounds.subprocess, "Popen", side_effect=launch):
                backgrounds.render_local(job, config)
            self.assertTrue(backgrounds.valid_png(job.path))
            self.assertEqual(list(root.glob("render-*")), [])


if __name__ == "__main__":
    unittest.main()


def test_background_figures_require_an_explicit_current_interaction():
    descriptions = [
        'A quiet woodland path beneath tall trees.',
        'A village square with deer visible near the forest.',
        'The party is currently interacting with a friendly dragon beside an old bridge.',
    ]
    for description in descriptions:
        scene = music.SceneContext.parse({'key': 'test-place', 'label': 'Test Place', 'description': description})
        prompt = backgrounds.prompt_for('forest', 'warm', scene=scene)
        assert description in prompt
        assert 'If no such active interaction is described, show scenery only.' in prompt
        assert 'A mere mention of a person or animal in the setting is not an interaction.' in prompt
        assert 'No bystanders, background people, crowds, unrelated wildlife or extra monsters.' in prompt
        assert 'at most two' not in prompt
