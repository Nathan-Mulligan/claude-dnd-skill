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


def test_named_scenes_vary_within_one_setting_and_hold_during_followup(display):
    module, client = display
    first = {"key": "clockwork-room", "label": "The Clockwork Room",
             "description": "A vaulted room of intricate brass mechanisms and gently chiming clocks."}
    second = {"key": "rooftop-gathering", "label": "The Rooftop Gathering",
              "description": "An open rooftop with silk canopies, lively music and a view of distant city towers."}
    def cue(scene, event):
        return client.post("/chunk", json={"text": "You arrive and look around.", "music": {
            "event": "scene", "id": event, "setting": "city", "mood": "warm", "scene": scene}})
    assert cue(first, "first").status_code == 204
    one = module._music.snapshot()
    assert one.get("scene") == first
    assert client.get("/health").json["scene"] == {"setting": "city", "mood": "warm", **first}
    client.post("/chunk", json={"text": "Forest, forest, forest. You tell a story about the forest."})
    assert module._music.snapshot() == one
    assert cue(first | {"description": "A changed description that must not restart this scene."}, "followup").status_code == 204
    assert module._music.snapshot() == one
    assert cue(second, "second").status_code == 204
    two = module._music.snapshot()
    assert two["scene_id"] != one["scene_id"]
    assert two["scene"] == second
    assert module._current_scene_name == "city"
    assert module._scene_payload(two)["label"] == second["label"]
    assert module._scene_payload(two)["particles"] == "dust"
    assert module._scene_payload(two)["scene_id"] == two["scene_id"]


@pytest.mark.parametrize("scene", [
    {"key": "partial"}, {"key": "bad/key", "label": "A", "description": "B"},
    {"key": "a", "label": "A", "description": "x" * 801},
    {"key": "a", "label": "A", "description": "B", "extra": "C"},
])
def test_invalid_named_scene_does_not_consume_event_or_change_media(display, scene):
    module, client = display
    before = module._music.snapshot()
    response = client.post("/music-cue", json={"event": "scene", "id": "reusable", "setting": "city", "scene": scene})
    assert response.status_code == 400
    assert module._music.snapshot() == before
    assert "reusable" not in module._music.seen


def test_named_prison_scene_reconnect_uses_dungeon_visual_style(display):
    import json
    module, client = display
    named = {"key": "quiet-cells", "label": "The Quiet Cells", "description": "A row of empty stone cells beneath the fortress."}
    assert client.post("/chunk", json={"text": "You arrive.", "music": {"event": "scene", "id": "cells", "setting": "prison", "scene": named}}).status_code == 204
    response = client.get("/stream", buffered=False)
    event = json.loads(next(response.response).decode().split("data: ", 1)[1])
    response.close()
    assert event["scene"]["name"] == "prison"
    assert event["scene"]["label"] == named["label"]
    assert event["scene"]["colors"] == module.SCENES["dungeon"]["colors"]


def test_theme_mood_is_inherited_through_both_http_cue_paths(display):
    module, client = display
    forest = {"key": "forest", "label": "Greenwood", "description": "Soft woodland light.",
              "theme": [{"name": "Greenwood", "mood": "warm"}]}
    assert client.post('/music-cue', json={"event": "scene", "id": "f", "setting": "forest", "scene": forest}).status_code == 200
    hut = {"key": "hut", "label": "The Hut", "description": "A timber cottage."}
    assert client.post('/chunk', json={"text": "The door opens.", "music": {"event": "scene", "id": "h", "setting": "tavern", "scene": hut}}).status_code == 204
    state = module._music.snapshot()
    assert state['mood'] == 'warm'
    assert state['scene']['theme'] == forest['theme']
    assert client.get('/health').json['scene']['theme'] == forest['theme']


def test_era_api_and_health_preserve_world_choice(display):
    module, client = display
    context = {'key': 'station', 'label': 'The Station', 'description': 'A glass transit hall.', 'era': 'futuristic'}
    response = client.post('/music-cue', json={'event': 'scene', 'id': 'era-one', 'setting': 'city', 'scene': context})
    assert response.status_code == 200
    assert client.get('/health').json['scene']['era'] == 'futuristic'
    assert client.post('/chunk', json={'text': 'You enter the cafe.', 'music': {
        'event': 'scene', 'id': 'era-two', 'setting': 'tavern', 'scene': context | {'key': 'cafe', 'era': 'modern'}}}).status_code == 204
    assert module._music.snapshot()['scene']['era'] == 'modern'


def test_wizard_tower_cue_generates_both_media_instead_of_retaining_previous_place(display, tmp_path, monkeypatch):
    import base64
    import wave
    module, client = display
    def render_audio(job):
        with wave.open(str(job.path), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b'\0\0' * 80)
    def render_image(job):
        job.path.write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+c8f8AAAAASUVORK5CYII='))
    images = module._background_module.BackgroundDirector(tmp_path / 'tower-images', renderer=render_image, autostart=False)
    monkeypatch.setattr(module, '_background', images)
    module._music.renderer = render_audio
    previous = module._music.transition('scene', 'previous-cavern', 'cave')
    tower = {'key': 'wizard-tower-entrance', 'label': 'The Wizard Tower',
             'description': 'A crooked ivy-covered stone wizard tower with a purple upstairs window.'}
    response = client.post('/chunk', json={'text': 'You reach the wizard tower.',
        'music': {'event': 'scene', 'id': 'tower-arrival', 'setting': 'tower', 'mood': 'curious', 'scene': tower}})
    assert response.status_code == 204
    state = module._music.snapshot()
    assert state['setting'] == 'arcane'
    assert state['scene'] == tower
    assert state['scene_id'] != previous['scene_id']
    while module._music.process_next():
        pass
    ready = module._music.snapshot()
    assert ready['intro_url'] and ready['bed_url'] and ready['scene_ready']
    assert images.process_next()
    assert images.snapshot()['status'] == 'ready'
    assert images.snapshot()['scene_id'] == ready['scene_id']
