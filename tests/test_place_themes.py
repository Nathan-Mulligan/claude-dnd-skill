"""Place atmosphere follows its ancestry and explicit local overrides."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/dnd/display'))
import backgrounds
import music

FOREST = [{"name": "Greenwood", "mood": "warm"}]


def scene(key, theme=None, *, include=True):
    value = {"key": key, "label": key, "description": "A small timber hut beneath woodland branches."}
    if include:
        value["theme"] = theme
    return value


def test_forest_hut_creepy_hut_and_return_share_effective_tone(tmp_path):
    director = music.MusicDirector(tmp_path, None, None, renderer=lambda job: None, autostart=False)
    forest = director.transition('scene', 'forest', 'forest', scene=scene('forest', FOREST))
    hut = director.transition('scene', 'hut', 'tavern', scene=scene('hut', FOREST + [{"name": "Clearing Hut"}]))
    assert forest['mood'] == hut['mood'] == 'warm'
    creepy = director.transition('scene', 'creepy', 'tavern', scene=scene('creepy', FOREST + [{"name": "Creepy Hut", "mood": "ominous"}]))
    assert creepy['mood'] == 'ominous'
    continuing = director.transition('scene', 'inside', 'tavern', scene=scene('inside', include=False))
    assert continuing['mood'] == 'ominous'
    assert continuing['scene']['theme'] == creepy['scene']['theme']
    returned = director.transition('scene', 'back', 'forest', scene=scene('forest', FOREST))
    assert returned['mood'] == 'warm'
    assert returned['scene_id'] != forest['scene_id']


def test_override_clear_dedup_and_restart(tmp_path):
    director = music.MusicDirector(tmp_path, None, None, renderer=lambda job: None, autostart=False)
    initial = director.transition('scene', 'a', 'forest', scene=scene('a', FOREST))
    assert director.transition('scene', 'same', 'forest', scene=scene('a', include=False))['scene_id'] == initial['scene_id']
    overridden = director.transition('scene', 'b', 'forest', 'tense', scene('a', FOREST))
    assert overridden['mood'] == 'tense'
    assert overridden['scene_id'] != initial['scene_id']
    assert director.transition('scene', 'continue-override', 'forest', scene=scene('a', include=False))['scene_id'] == overridden['scene_id']
    assert director.transition('scene', 'continue-explicit-path', 'forest', scene=scene('a', FOREST))['mood'] == 'tense'
    base_again = director.transition('scene', 'next', 'forest', scene=scene('new-action', include=False))
    assert base_again['mood'] == 'warm'
    director.transition('scene', 'restore-override', 'forest', 'tense', scene('new-action', FOREST))
    restored = music.MusicDirector(tmp_path, None, None, autostart=False)
    assert restored.snapshot()['scene']['theme'] == FOREST
    restored.transition('combat_start', 'fight')
    assert restored.transition('combat_end', 'peace')['mood'] == 'tense'
    cleared = restored.transition('scene', 'clear', 'city', scene=scene('city', []))
    assert cleared['mood'] == 'ominous'
    assert cleared['scene']['theme'] == []
    legacy = music.SceneContext.parse(scene('old', include=False))
    assert legacy.to_dict() == scene('old', include=False)


@pytest.mark.parametrize('theme', [None, 'warm', [{}], [{'name': ''}], [{'name': 'Forest', 'mood': 'wrong'}], [{'name': 'x'}] * 9, [{'name': 'x', 'extra': 1}]])
def test_bad_themes_do_not_consume_event(tmp_path, theme):
    director = music.MusicDirector(tmp_path, None, None, autostart=False)
    with pytest.raises(ValueError):
        director.transition('scene', 'retry', 'forest', scene=scene('forest', theme))
    assert 'retry' not in director.seen


def test_shared_render_profile_and_frozen_inherited_path(tmp_path):
    director = music.MusicDirector(tmp_path, None, None, renderer=lambda job: None, autostart=False)
    path = [dict(level) for level in FOREST] + [{'name': 'Hut'}]
    state = director.transition('scene', 'a', 'tavern', scene=scene('hut', path))
    context = music.SceneContext.parse(state['scene'])
    path[0]['mood'] = 'ominous'
    assert director.snapshot()['mood'] == 'warm'
    assert director.snapshot()['scene']['theme'][0]['mood'] == 'warm'
    image = backgrounds.prompt_for('tavern', state['mood'], scene=context)
    audio = list(director.jobs.queue)[0].prompt
    profile = music.MOOD_PROFILES['warm']
    assert profile['atmosphere'] in image and profile['atmosphere'] in audio
    assert profile['visual'] in image
    assert profile['music'] in audio
    assert 'dark reading area' in image
    assert 'Greenwood' in image and 'Greenwood' in audio


def test_background_reloads_same_effective_theme_and_jobs_keep_original_context(tmp_path):
    director = music.MusicDirector(tmp_path/'music', None, None, renderer=lambda job: None, autostart=False)
    images = backgrounds.BackgroundDirector(tmp_path/'images', renderer=lambda job: None, autostart=False)
    first = director.transition('scene', 'a', 'forest', scene=scene('forest', FOREST))
    images.observe_music(first | {'scene_ready': True})
    job = images.jobs.get_nowait()
    second = director.transition('scene', 'b', 'tavern', scene=scene('hut', FOREST + [{'name':'Hut','mood':'ominous'}]))
    images.observe_music(second | {'scene_ready': True})
    assert job.mood == 'warm'
    assert job.scene.to_dict()['theme'] == FOREST
    restored = backgrounds.BackgroundDirector(tmp_path/'images', renderer=lambda job: None, autostart=False)
    assert restored.snapshot()['mood'] == 'ominous'
    assert restored.snapshot()['scene']['theme'] == second['scene']['theme']
