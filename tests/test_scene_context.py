"""Named scene identity varies media independently of broad setting categories."""

import base64
import sys
import wave
from dataclasses import asdict
from pathlib import Path

import pytest

DISPLAY = Path(__file__).resolve().parents[1] / "skills/dnd/display"
sys.path.insert(0, str(DISPLAY))
import backgrounds
import music


FIRST = {"key": "clockwork-room", "label": "The Clockwork Room",
         "description": "A vaulted interior of intricate brass mechanisms and gently chiming clocks."}
SECOND = {"key": "rooftop-gathering", "label": "The Rooftop Gathering",
          "description": "An open rooftop with silk canopies, lively music and distant city towers."}


def audio(job):
    with wave.open(str(job.path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\0\0" * 80)


def image(job):
    job.path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+c8f8AAAAASUVORK5CYII="))


def test_specific_scene_changes_both_media_and_continuation_does_not(tmp_path):
    audio_jobs, image_jobs = [], []
    images = backgrounds.BackgroundDirector(tmp_path / "images", renderer=lambda job: (image_jobs.append(job), image(job)), autostart=False)
    director = music.MusicDirector(tmp_path / "music", None, None, renderer=lambda job: (audio_jobs.append(job), audio(job)), autostart=False)
    director.set_broadcast(lambda payload: images.observe_music(payload["music"]))
    first = director.transition("scene", "a", "city", "curious", FIRST)
    while director.process_next():
        pass
    assert images.process_next()
    one = images.snapshot()
    assert one["scene"] == FIRST
    jobs_before = (len(audio_jobs), len(image_jobs))
    assert director.transition("scene", "b", "city", "tense", FIRST | {"description": "Changed words"}) == director.snapshot()
    assert not director.process_next()
    assert not images.process_next()
    assert (len(audio_jobs), len(image_jobs)) == jobs_before
    second = director.transition("scene", "c", "city", "warm", SECOND)
    while director.process_next():
        pass
    assert images.process_next()
    two = images.snapshot()
    assert two["scene"] == SECOND
    assert one["scene_id"] != two["scene_id"]
    assert first["entry"] != second["entry"]
    assert FIRST["description"] in audio_jobs[0].prompt
    assert SECOND["description"] in audio_jobs[2].prompt
    assert backgrounds.prompt_for("city", "curious", scene=image_jobs[0].scene) != backgrounds.prompt_for("city", "warm", scene=image_jobs[1].scene)
    assert backgrounds.ENVIRONMENTS["city"] not in backgrounds.prompt_for("city", "curious", scene=image_jobs[0].scene)
    restored = music.MusicDirector(tmp_path / "music", None, None, renderer=audio, autostart=False)
    assert restored.snapshot()["scene"] == SECOND
    assert restored.transition("scene", "d", "city", "warm", SECOND)["scene_id"] == second["scene_id"]
    assert restored.jobs.qsize() == 0
    restored_images = backgrounds.BackgroundDirector(tmp_path / "images", renderer=image, autostart=False)
    assert restored_images.snapshot()["scene"] == SECOND


def test_pending_combat_scene_restores_context_then_reset_and_reentry_refresh(tmp_path):
    director = music.MusicDirector(tmp_path, None, None, renderer=audio, autostart=False)
    first = director.transition("scene", "first", "city", "curious", FIRST)
    director.transition("combat_start", "fight")
    pending = director.transition("scene", "pending", "city", "tense", SECOND)
    assert pending["mode"] == "combat"
    restored = music.MusicDirector(tmp_path, None, None, renderer=audio, autostart=False)
    assert restored.snapshot()["scene"] == SECOND
    returned = restored.transition("combat_end", "peace")
    assert returned["scene_id"] == pending["scene_id"]
    assert returned["scene"] == SECOND
    assert any(SECOND["description"] in job.prompt for job in list(restored.jobs.queue) if job.kind == "intro")
    reentered = restored.transition("scene", "back", "city", "curious", FIRST)
    assert reentered["scene_id"] != first["scene_id"]
    assert reentered["scene"] == FIRST
    restored.reset()
    assert restored.snapshot()["scene"] is None
    assert restored.transition("scene", "again", "city", "curious", FIRST)["scene_id"] != reentered["scene_id"]


def test_context_is_frozen_and_legacy_and_dark_fantasy_remain_supported(tmp_path):
    value = FIRST.copy()
    director = music.MusicDirector(tmp_path, None, None, renderer=audio, autostart=False)
    first = director.transition("scene", "first", "city", "warm", value)
    value["description"] = "A later mutation"
    assert director.snapshot()["scene"] == FIRST
    assert FIRST["description"] in list(director.jobs.queue)[0].prompt
    context = music.SceneContext.parse(FIRST)
    assert backgrounds.SceneContext is music.SceneContext
    assert asdict(context) == FIRST
    assert "dark fantasy" not in music.prompt_for("city", "warm", "intro", context)
    assert "dark fantasy" in music.prompt_for("city", "ominous", "intro", context)
    legacy = director.transition("scene", "legacy", "city")
    assert legacy["scene"] is None
    assert legacy["scene_id"] != first["scene_id"]
    assert director.transition("scene", "legacy-again", "city")["scene_id"] != legacy["scene_id"]


@pytest.mark.parametrize("change", [
    {"key": "x" * 65}, {"key": "not/a/path"}, {"label": "x" * 121},
    {"description": "x" * 801}, {"description": ""}, {"label": []},
])
def test_context_limits_reject_before_remembering(tmp_path, change):
    director = music.MusicDirector(tmp_path, None, None, autostart=False)
    with pytest.raises(ValueError):
        director.transition("scene", "retry", "city", "warm", FIRST | change)
    assert "retry" not in director.seen
    assert director.snapshot()["mode"] == "idle"
