"""Observable scene-music transition and serving behavior."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

DISPLAY = Path(__file__).resolve().parent.parent / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))
import music


def fake_render(job: music.Job) -> None:
    with wave.open(str(job.path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(44100)
        out.writeframes(b"\0" * 1764)


class MusicDirectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.events = []
        self.director = music.MusicDirector(Path(self.temp.name), None, None,
                                            renderer=fake_render, autostart=False)
        self.director.set_broadcast(self.events.append)

    def drain(self) -> None:
        while self.director.process_next():
            pass

    def test_scene_intro_bed_combat_and_return_get_fresh_entries(self) -> None:
        first = self.director.transition("scene", "event-1", "cave", "ominous")
        self.assertEqual((first["mode"], first["intro_url"], first["bed_url"]),
                         ("explore", None, None))
        self.drain()
        ready = self.director.snapshot()
        self.assertTrue(ready["intro_url"])
        self.assertTrue(ready["bed_url"])
        combat = self.director.transition("combat_start", "event-2")
        self.assertEqual(combat["mode"], "combat")
        self.assertIsNone(combat["bed_url"])
        self.drain()
        self.assertTrue(self.director.snapshot()["combat_url"])
        after = self.director.transition("combat_end", "event-3")
        self.assertEqual((after["setting"], after["intro_url"]), ("cave", None))
        self.drain()
        self.assertTrue(self.director.snapshot()["bed_url"])
        second = self.director.transition("scene", "event-4", "cave", "ominous")
        self.assertNotEqual(second["entry"], first["entry"])
        self.assertIsNone(self.director.file_for(first["entry"], "bed"))

    def test_duplicate_and_repeated_combat_status_do_not_render_twice(self) -> None:
        first = self.director.transition("scene", "event-1", "forest")
        again = self.director.transition("scene", "event-1", "forest")
        self.assertEqual(first, again)
        self.assertEqual(self.director.jobs.qsize(), 2)
        combat = self.director.transition("combat_start", "event-2")
        repeat = self.director.transition("combat_start", "stats-event")
        self.assertEqual(combat, repeat)
        self.assertEqual(self.director.jobs.qsize(), 3)

    def test_old_jobs_do_not_publish_or_serve(self) -> None:
        old = self.director.transition("scene", "event-1", "forest")
        current = self.director.transition("scene", "event-2", "prison")
        self.drain()
        self.assertEqual(self.director.snapshot()["entry"], current["entry"])
        self.assertIsNone(self.director.file_for(old["entry"], "intro"))
        self.assertFalse(any(e["music"]["entry"] == old["entry"] and
                             e["music"]["intro_url"] for e in self.events))

    def test_restart_reuses_current_files_and_duplicate_id(self) -> None:
        first = self.director.transition("scene", "event-1", "cave")
        self.drain()
        restarted = music.MusicDirector(Path(self.temp.name), None, None,
                                        renderer=fake_render, autostart=False)
        self.assertEqual(restarted.snapshot()["entry"], first["entry"])
        self.assertTrue(restarted.snapshot()["bed_url"])
        self.assertEqual(restarted.transition("scene", "event-1", "cave")["entry"],
                         first["entry"])
        self.assertEqual(restarted.jobs.qsize(), 0)

    def test_idle_revision_survives_restart(self) -> None:
        self.director.transition("scene", "scene-1", "cave")
        self.director.reset()
        restarted = music.MusicDirector(Path(self.temp.name), None, None,
                                        renderer=fake_render, autostart=False)
        self.assertEqual(restarted.snapshot()["revision"], self.director.snapshot()["revision"])
        self.assertEqual(restarted.snapshot()["mode"], "idle")

    def test_scene_cue_during_combat_only_updates_remembered_setting(self) -> None:
        self.director.transition("scene", "scene-1", "cave")
        combat = self.director.transition("combat_start", "combat-1")
        pending = self.director.jobs.qsize()
        update = self.director.transition("scene", "scene-2", "forest", "curious")
        self.assertEqual(update["entry"], combat["entry"])
        self.assertEqual(update["mode"], "combat")
        self.assertEqual(update["setting"], "forest")
        self.assertEqual(self.director.jobs.qsize(), pending)
        after = self.director.transition("combat_end", "combat-2")
        self.assertEqual(after["setting"], "forest")
        self.assertEqual(after["mode"], "explore")

    def test_restart_requeues_missing_current_jobs_only(self) -> None:
        first = self.director.transition("scene", "scene-1", "cave")
        restarted = music.MusicDirector(Path(self.temp.name), None, None,
                                        renderer=fake_render, autostart=False)
        self.assertEqual(restarted.snapshot()["entry"], first["entry"])
        self.assertEqual(restarted.jobs.qsize(), 2)
        while restarted.process_next():
            pass
        self.assertTrue(restarted.snapshot()["intro_url"])
        self.assertTrue(restarted.snapshot()["bed_url"])

    def test_scene_superseded_during_render_cannot_play_old_bed(self) -> None:
        def interrupted_render(job: music.Job) -> None:
            fake_render(job)
            if job.kind == "intro":
                self.director.transition("combat_start", "combat-1")
        self.director.renderer = interrupted_render
        old = self.director.transition("scene", "scene-1", "cave")
        self.drain()
        state = self.director.snapshot()
        self.assertEqual(state["mode"], "combat")
        self.assertIsNone(state["bed_url"])
        self.assertTrue(state["combat_url"])
        self.assertIsNone(self.director.file_for(old["entry"], "intro"))
        self.assertFalse((Path(self.temp.name) / f"{old['entry']}-bed.wav").exists())

    def test_prunes_only_old_owned_wavs_and_keeps_current_entry(self) -> None:
        old_paths = []
        for index, digit in enumerate("abcd"):
            path = Path(self.temp.name) / f"{digit * 32}-bed.wav"
            path.write_bytes(b"RIFF")
            os.utime(path, (100 + index, 100 + index))
            old_paths.append(path)
        unrelated = Path(self.temp.name) / "other-file.wav"
        unrelated.write_text("keep", encoding="utf-8")
        current = self.director.transition("scene", "current", "cave")
        self.drain()
        self.director._prune_old()
        self.assertFalse(old_paths[0].exists())
        self.assertFalse(old_paths[1].exists())
        self.assertTrue(old_paths[2].exists())
        self.assertTrue(old_paths[3].exists())
        self.assertTrue(self.director.file_for(current["entry"], "bed"))
        self.assertTrue(unrelated.exists())

    def test_invalid_cue_does_not_change_state(self) -> None:
        with self.assertRaises(ValueError):
            self.director.transition("scene", "bad", "../../outside")
        self.assertEqual(self.director.snapshot()["mode"], "idle")

    def test_generated_file_route_uses_only_current_entry(self) -> None:
        with tempfile.TemporaryDirectory() as rt:
            with mock.patch.dict(os.environ, {"DND_RUNTIME_DIR": rt}):
                spec = importlib.util.spec_from_file_location(
                    "_music_app_under_test", DISPLAY / "dnd-display-app.py")
                assert spec and spec.loader
                appmod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = appmod
                spec.loader.exec_module(appmod)
                appmod._music = self.director
                client = appmod.app.test_client()
                response = client.post("/chunk", json={"text": "You enter a cave.",
                    "music": {"event": "scene", "id": "route-1", "setting": "cave", "mood": "ominous"}})
                self.assertEqual(response.status_code, 204)
                self.drain()
                state = self.director.snapshot()
                response = client.get(state["intro_url"])
                self.assertEqual(response.status_code, 200)
                response.close()
                self.assertEqual(client.get("/audio/music/not-current/intro").status_code, 404)
                self.assertEqual(client.get("/audio/music/../../etc/passwd").status_code, 404)
                appmod._lan_token = "local-test-token"
                self.assertEqual(client.get(state["intro_url"]).status_code, 403)
                authorized = client.get(state["intro_url"], headers={"X-DND-Token": "local-test-token"})
                self.assertEqual(authorized.status_code, 200)
                authorized.close()
                appmod._lan_token = None
                self.assertEqual(client.post("/stats", json={"turn_order": {"current": "A", "round": 1}}).status_code, 204)
                self.assertEqual(self.director.snapshot()["mode"], "combat")
                self.assertEqual(client.post("/stats", json={"turn_order": {"current": "B", "round": 1}}).status_code, 204)
                self.assertEqual(self.director.snapshot()["mode"], "combat")
                self.assertEqual(client.post("/stats", json={"turn_order": None}).status_code, 204)
                self.assertEqual(self.director.snapshot()["mode"], "explore")


if __name__ == "__main__":
    unittest.main()
