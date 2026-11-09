"""World-era guidance stays explicit and survives scene lifecycle changes."""

import sys
from pathlib import Path

import pytest

DISPLAY = Path(__file__).resolve().parents[1] / "skills/dnd/display"
sys.path.insert(0, str(DISPLAY))
import backgrounds
import music

SCENE = {"key": "inn", "label": "The Inn", "description": "A tavern with brass clockwork constructs and a stone hearth."}


def director(path):
    return music.MusicDirector(path, None, None, renderer=lambda job: None, autostart=False)


def test_default_image_prompt_uses_tested_period_guidance():
    prompt = backgrounds.prompt_for("tavern", "warm", scene=music.SceneContext.parse(SCENE))
    assert "A pre-industrial medieval fantasy world" in prompt
    assert "rough-hewn timber" in prompt
    assert "hand-forged iron" in prompt
    assert "brass clockwork constructs" in prompt
    assert "dark reading area" in prompt
    assert music.SceneContext.parse(SCENE).to_dict() == SCENE


@pytest.mark.parametrize("era", ["modern", "futuristic", "Victorian steampunk"])
def test_explicit_era_replaces_medieval_defaults(era):
    scene = music.SceneContext.parse(SCENE | {"era": era})
    prompt = backgrounds.prompt_for("city", "warm", scene=scene)
    assert era in prompt
    assert "long-sleeved linen tunics" not in prompt
    assert "pre-industrial medieval" not in prompt
    assert "illustrated fantasy panorama" not in prompt
    assert "fantasy atmosphere" not in prompt
    assert scene.to_dict()["era"] == era


@pytest.mark.parametrize("era", ["", " ", "x" * 101, [], "modern\nmedieval", "modern\x00"])
def test_invalid_era_cannot_consume_event(tmp_path, era):
    d = director(tmp_path)
    with pytest.raises(ValueError):
        d.transition("scene", "retry", "city", "warm", SCENE | {"era": era})
    assert "retry" not in d.seen
    assert d.snapshot()["mode"] == "idle"


def test_era_inherits_and_survives_restart_and_combat(tmp_path):
    d = director(tmp_path)
    first = d.transition("scene", "one", "city", "tense", SCENE | {"era": "modern"})
    queued = list(d.jobs.queue)[0].prompt
    second = d.transition("scene", "two", "tavern", None, SCENE | {"key": "room"})
    assert second["scene"]["era"] == "modern"
    restored = director(tmp_path)
    assert restored.snapshot()["scene"]["era"] == "modern"
    restored.transition("combat_start", "fight")
    assert restored.transition("combat_end", "peace")["scene"]["era"] == "modern"
    assert first["scene"]["era"] == "modern"
    assert list(d.jobs.queue)[0].prompt == queued
    restored.reset()
    assert restored.transition("scene", "new-campaign", "city", scene=SCENE)["scene"] == SCENE


def test_era_correction_refreshes_current_scene_without_dropping_mood(tmp_path):
    d = director(tmp_path)
    first = d.transition("scene", "one", "city", "tense", SCENE | {"era": "medieval"})
    corrected = d.transition("scene", "two", "city", None, SCENE | {"era": "modern"})
    assert corrected["scene_id"] != first["scene_id"]
    assert corrected["mood"] == "tense"
    continued = d.transition("scene", "three", "city", None, SCENE | {"description": "Changed words"})
    assert continued["scene_id"] == corrected["scene_id"]
    assert continued["scene"]["era"] == "modern"


def test_queued_image_context_retains_era_after_payload_changes(tmp_path):
    import base64
    jobs = []
    def render(job):
        jobs.append(job)
        job.path.write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+c8f8AAAAASUVORK5CYII='))
    images = backgrounds.BackgroundDirector(tmp_path, renderer=render, autostart=False)
    state = {'revision': 1, 'scene_revision': 1, 'scene_id': 'a' * 32, 'setting': 'city', 'mood': 'warm',
             'scene_ready': True, 'scene': SCENE | {'era': 'modern'}}
    images.observe_music(state)
    state['scene']['era'] = 'futuristic'
    assert images.process_next()
    assert jobs[0].scene.era == 'modern'
    assert images.snapshot()['scene']['era'] == 'modern'
    restored = backgrounds.BackgroundDirector(tmp_path, renderer=render, autostart=False)
    assert restored.snapshot()['scene']['era'] == 'modern'
