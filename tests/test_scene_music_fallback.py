"""Ordinary DM narration starts scene media without an optional music flag."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


DISPLAY = Path(__file__).resolve().parents[1] / "skills/dnd/display"


@pytest.fixture
def display(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path / ".runtime"))
    monkeypatch.syspath_prepend(str(DISPLAY))
    import runtime_paths
    (tmp_path / ".runtime").mkdir(exist_ok=True)
    monkeypatch.setattr(runtime_paths, "RT", str(tmp_path / ".runtime"))
    spec = importlib.util.spec_from_file_location("_scene_music_fallback_test", DISPLAY / "dnd-display-app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module._music = module._music_module.MusicDirector(tmp_path / "music-test", None, None,
                                                      renderer=lambda job: None, autostart=False)
    module._music.set_broadcast(module._broadcast_music)
    module._audio = None
    module._broadcast = lambda payload: None
    return module, module.app.test_client()


def test_first_narration_starts_intro_and_loop_once(display):
    module, client = display
    assert client.post("/chunk", json={"text": "Your adventure begins."}).status_code == 204
    first = module._music.snapshot()
    assert first["mode"] == "explore"
    assert first["setting"] == "tavern"
    assert module._music.jobs.qsize() == 2
    client.post("/chunk", json={"text": "You pause and consider your choices."})
    assert module._music.snapshot()["revision"] == first["revision"]
    assert module._music.jobs.qsize() == 2


def test_established_scene_change_starts_new_music(display, monkeypatch):
    module, client = display
    module._music.transition("scene", "old", "tavern")
    monkeypatch.setattr(module, "_detect_scene", lambda text: module.SCENES["forest"] | {"name": "forest"})
    client.post("/chunk", json={"text": "You enter the ancient woodland."})
    assert module._music.snapshot()["setting"] == "forest"
    assert module._music.jobs.qsize() == 4


def test_explicit_cue_is_authoritative_without_duplicate_fallback(display, monkeypatch):
    module, client = display
    monkeypatch.setattr(module, "_detect_scene", lambda text: module.SCENES["forest"] | {"name": "forest"})
    client.post("/chunk", json={"text": "Trees line the distant shore.",
                               "music": {"event": "scene", "id": "coast", "setting": "ocean", "mood": "calm"}})
    assert module._music.snapshot()["setting"] == "ocean"
    assert module._current_scene_name == "ocean"
    assert module._music.jobs.qsize() == 2


@pytest.mark.parametrize("flag", ["player", "npc", "dice", "action", "tutor"])
def test_non_narration_never_starts_scene_music(display, flag):
    module, client = display
    client.post("/chunk", json={"text": "Forest cave castle", flag: "Hero" if flag != "dice" else True})
    assert module._music.snapshot()["mode"] == "idle"
    assert module._music.jobs.empty()


def test_setup_question_does_not_start_scene_music(display):
    module, client = display
    client.post("/setup-question", json={"text": "Forest or castle?"})
    assert module._music.jobs.empty()


def test_combat_does_not_implicitly_change_scene(display, monkeypatch):
    module, client = display
    module._music.transition("scene", "cave", "cave")
    module._music.transition("combat_start", "fight")
    before = module._music.snapshot()
    monkeypatch.setattr(module, "_detect_scene", lambda text: pytest.fail("Combat must retain its scene"))
    client.post("/chunk", json={"text": "A forest of spears meets your charge."})
    assert module._music.snapshot() == before


def test_malformed_optional_cue_does_not_block_fallback(display):
    module, client = display
    client.post("/chunk", json={"text": "Your adventure begins.", "music": {"event": "unsupported"}})
    assert module._music.snapshot()["mode"] == "explore"
    assert module._music.jobs.qsize() == 2


def test_sound_scan_receives_current_character_names(display):
    module, client = display
    module._current_stats["players"] = [{"name": "Karate Kick"}]
    module._audio = SimpleNamespace(on_text=mock.Mock(), on_scene_change=mock.Mock())
    client.post("/chunk", json={"text": "Karate Kick greets the soldier."})
    module._audio.on_text.assert_called_once_with("Karate Kick greets the soldier.", ignored_names=["Karate Kick"])


def test_music_can_be_started_without_adding_story_text(display):
    module, client = display
    before = list(module._text_log)
    response = client.post("/music-cue", json={"event": "scene", "id": "manual", "setting": "city", "mood": "warm"})
    assert response.status_code == 200
    assert response.json["setting"] == "city"
    assert module._current_scene_name == "city"
    assert module._music.jobs.qsize() == 2
    assert list(module._text_log) == before


def test_restart_restores_scene_heading_from_saved_music(display, tmp_path, monkeypatch):
    module, _ = display
    saved = module._music_module.MusicDirector(tmp_path / ".runtime/music", None, None,
                                              renderer=lambda job: None, autostart=False)
    saved.transition("scene", "saved-mountain", "mountain", "warm")
    spec = importlib.util.spec_from_file_location("_restored_scene_heading_test", DISPLAY / "dnd-display-app.py")
    restarted = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, restarted)
    spec.loader.exec_module(restarted)
    assert restarted._current_scene_name == "mountain"
    assert restarted._music.snapshot()["setting"] == "mountain"


def test_delayed_music_updates_cannot_restore_an_old_scene(display):
    module, _ = display
    old = module._music.transition("scene", "old-cave", "cave")
    new = module._music.transition("scene", "new-mountain", "mountain")
    module._broadcast_music({"music": old})
    assert module._current_scene_name == new["setting"] == "mountain"
    module._music.reset()
    module._current_scene_name = "tavern"
    module._broadcast_music({"music": new})
    assert module._current_scene_name == "tavern"


@pytest.mark.parametrize("payload", [["scene"], {"event": [], "id": "bad"}, {"event": "scene", "id": "bad", "setting": []}])
def test_invalid_standalone_cue_does_not_start_media(display, payload):
    module, client = display
    assert client.post("/music-cue", json=payload).status_code == 400
    assert module._music.jobs.empty()
