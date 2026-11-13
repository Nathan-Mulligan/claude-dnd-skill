"""One scene engine turns a short DM cue into the scene that music and images share."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

DISPLAY = Path(__file__).resolve().parents[1] / "skills/dnd/display"
sys.path.insert(0, str(DISPLAY))
import backgrounds
import music
import scene_engine
from scene_engine import PlaceTheme, WorldNotes, derive, is_continuation

SPEC = importlib.util.spec_from_file_location("dnd_send_scene", DISPLAY / "send.py")
send = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(send)

ENDERAL = """# Enderal — World
## World Foundations
Era: medieval. Medium magic: uncommon trained practitioners.

## Place Themes
Brackenford: [{"name":"Brackenford","mood":"tense"}]. West Gate: child {"name":"West Gate"}. Mill: child {"name":"Mill","mood":"warm"}. The tension is economic.

Saltmere: [{"name":"Saltmere","mood":"curious"}]. Meren’s Bakery: child {"name":"Meren’s Bakery","mood":"warm"}. Western salt-working town.

## Three Truths
"""

MACHINES = """# World: Machines
## Campaign Tone & Genre
- **Setting type:** medieval castles and knights with whimsical machines
## World Foundations
- The Crystal Cavern contains glowing crystals. Scene theme: Rainbow Mountains (warm) → Crystal Cavern (curious).
- The Gloaming Forest is dim. Scene theme for revisits: Gloaming Forest (curious) → Forest Path; gently mysterious, never frightening.
- The Sunlit Glade is warm. Scene theme: Gloaming Forest (curious) → Sunlit Glade (warm).
- Established era: medieval fantasy, with explicitly established magical and clockwork machines.
"""


def test_same_place_gives_the_same_key_whatever_is_happening():
    world = WorldNotes.parse(ENDERAL)
    first = derive("Saltmere, Meren's Bakery: haggling over flour by the ovens", world=world)
    second = derive("Meren’s bakery in Saltmere: the ovens cool at dusk", world=world)
    assert first.scene.key == second.scene.key == "saltmere.merens-bakery"
    plain = [derive(f"The Old Mill: {doing}").scene.key for doing in ("grinding grain", "a quiet night")]
    assert plain == ["old-mill", "old-mill"]


def test_keys_stay_valid_for_long_or_non_latin_places():
    long_key = derive("A " + "very " * 40 + "long road").scene.key
    assert len(long_key) <= 64 and long_key == derive("A " + "very " * 40 + "long road: dusk").scene.key
    cjk = derive("酒馆: 午夜").scene
    assert cjk.key.startswith("scene-") and cjk.key == derive("酒馆: 清晨").scene.key
    music.SceneContext.parse(cjk.to_dict())


@pytest.mark.parametrize("cue,setting", [
    ("The Prancing Pony inn: a fiddler plays", "tavern"),
    ("Saltmere market square: stalls open", "city"),
    ("Crystal Cavern: light glimmers on a pool", "cave"),
    ("The ashen crypt: bones underfoot", "crypt"),
    ("The wizard tower: books float past", "arcane"),
    ("Deck of the Grey Gull: rain on the sails", "ocean"),
    ("The salt marsh: reeds hiss in the wind", "swamp"),
    ("Abbey of Saint Ena: monks at prayer", "temple"),
])
def test_setting_is_mapped_from_the_place(cue, setting):
    assert derive(cue).setting == setting


def test_unknown_setting_keeps_the_current_one_or_defaults_to_city():
    assert derive("Somewhere odd: things happen", current={"setting": "forest", "key": "x"}).setting == "forest"
    assert derive("Somewhere odd: things happen").setting == "city"


@pytest.mark.parametrize("notes,era", [
    (ENDERAL, "medieval"),
    (MACHINES, "medieval"),
    ("## Campaign Tone & Genre\n- **Tone:** dark\n- **Setting type:** futuristic\n", "futuristic"),
    ("Era: Victorian steampunk. Airships everywhere.\n", "Victorian steampunk"),
    ("- **Setting type:** <medieval / renaissance / ancient / nautical / underground>\n", None),
    ("- **Setting type:** nautical\n", None),
    ("", None),
])
def test_era_comes_from_world_notes(notes, era):
    assert WorldNotes.parse(notes).era == era


def test_world_era_wins_and_missing_era_inherits_the_current_scene():
    current = {"setting": "city", "key": "x", "era": "modern"}
    assert derive("Station: trains", world=WorldNotes.parse("Era: futuristic.\n"), current=current).scene.era == "futuristic"
    assert derive("Station: trains", current=current).scene.era == "modern"
    assert derive("Station: trains").scene.era is None


def test_known_places_take_their_saved_theme_path_and_mood():
    cue = derive("Saltmere, Meren's Bakery: flour dust in the air", world=WorldNotes.parse(ENDERAL))
    assert cue.scene.theme == (PlaceTheme("Saltmere", "curious"), PlaceTheme("Meren’s Bakery", "warm"))
    assert cue.mood == "warm"
    glade = derive("Sunlit Glade: butterflies over the stream", world=WorldNotes.parse(MACHINES))
    assert glade.scene.theme == (PlaceTheme("Gloaming Forest", "curious"), PlaceTheme("Sunlit Glade", "warm"))
    assert glade.scene.key == "gloaming-forest.sunlit-glade"
    path = derive("Gloaming Forest path: mushrooms glow", world=WorldNotes.parse(MACHINES))
    assert path.scene.theme == (PlaceTheme("Gloaming Forest", "curious"), PlaceTheme("Forest Path"))
    assert path.mood == "curious"


def test_unsaved_places_nest_under_a_named_or_current_region():
    world = WorldNotes.parse(ENDERAL)
    docks = derive("Saltmere docks: gulls over the brine sheds", world=world)
    assert docks.scene.theme == (PlaceTheme("Saltmere", "curious"), PlaceTheme("Saltmere docks"))
    assert docks.mood == "curious"
    current = {"setting": "city", "key": "saltmere.merens-bakery", "mood": "warm",
               "theme": [{"name": "Saltmere", "mood": "curious"}, {"name": "Meren’s Bakery", "mood": "warm"}]}
    alley = derive("A narrow back alley: a cat watches", world=world, current=current)
    assert alley.scene.theme == (PlaceTheme("Saltmere", "curious"), PlaceTheme("A narrow back alley"))
    assert derive("A narrow back alley: quiet").scene.theme is None


def test_mood_is_explicit_then_place_then_setting_default():
    world = WorldNotes.parse(ENDERAL)
    assert derive("Brackenford mill: wheels turn", "ominous", world=world).mood == "ominous"
    assert derive("Brackenford mill: wheels turn", world=world).mood == "warm"
    assert derive("The Prancing Pony inn: songs").mood == "warm"
    assert derive("The ashen crypt: dust").mood == "ominous"


def test_same_place_cue_is_a_no_op_unless_the_mood_or_era_changes():
    world = WorldNotes.parse(ENDERAL)
    cue = derive("Saltmere, Meren's Bakery: buying bread", world=world)
    current = {"setting": cue.setting, "mood": "warm", **cue.scene.to_dict()}
    again = derive("Meren's Bakery in Saltmere: the baker laughs", world=world, current=current)
    assert is_continuation(again, current, None)
    assert is_continuation(again, current, "warm")
    assert not is_continuation(derive("Meren's Bakery in Saltmere: fire!", "tense", world=world, current=current),
                               current, "tense")
    assert not is_continuation(derive("Saltmere docks: gulls", world=world, current=current), current, None)
    assert not is_continuation(cue, None, None)
    assert not is_continuation(cue, current | {"era": "modern"}, None)


@pytest.mark.parametrize("cue", ["", "   ", ": nothing named", "x" * 801, "line\nbreak"])
def test_invalid_cues_are_rejected(cue):
    with pytest.raises(ValueError):
        derive(cue)


def test_world_notes_load_reads_world_md_and_tolerates_missing_files(tmp_path):
    (tmp_path / "world.md").write_text(ENDERAL, encoding="utf-8")
    assert WorldNotes.load(tmp_path).era == "medieval"
    assert WorldNotes.load(tmp_path / "missing") == WorldNotes()


def test_music_and_backgrounds_draw_from_the_shared_engine(tmp_path):
    assert music.SceneContext is backgrounds.SceneContext is scene_engine.SceneContext
    assert music.prompt_for is scene_engine.music_prompt
    assert backgrounds.prompt_for is scene_engine.image_prompt
    assert music.SETTINGS is backgrounds.SETTINGS is scene_engine.SETTINGS

    cue = derive("Saltmere, Meren's Bakery: flour dust", world=WorldNotes.parse(ENDERAL))
    director = music.MusicDirector(tmp_path / "music", None, None, renderer=lambda job: None, autostart=False)
    state = director.transition("scene", "derived", **cue.music_fields())
    assert state["scene"] == cue.scene.to_dict() and state["mood"] == "warm" and state["setting"] == "city"
    job = director.jobs.get_nowait()
    assert job.prompt == scene_engine.music_prompt("city", "warm", job.kind, cue.scene)
    images = backgrounds.BackgroundDirector(tmp_path / "images", renderer=lambda job: None, autostart=False)
    images.observe_music(state | {"scene_ready": True})
    queued = images.jobs.get_nowait()
    assert queued.scene == cue.scene
    expected = scene_engine.image_prompt("city", "warm", scene=cue.scene)
    assert "Meren’s Bakery" in expected and "flour dust" in expected


def _run_send(monkeypatch, argv, health, text="The bell over the door rings."):
    monkeypatch.setattr(sys, "argv", ["send.py", *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    monkeypatch.setattr(send, "_ensure_active_campaign", lambda: None)
    monkeypatch.setattr(send, "_read_token", lambda: "")
    monkeypatch.setattr(send, "_verify_health", lambda: health)
    payloads = []
    monkeypatch.setattr(send, "_post", lambda url, data, token: payloads.append(json.loads(data)) or True)
    send.main()
    return payloads


def test_send_scene_derives_the_full_cue_from_health_and_world_notes(tmp_path, monkeypatch, capsys):
    campaign = tmp_path / "campaigns" / "enderal"
    campaign.mkdir(parents=True)
    (campaign / "world.md").write_text(ENDERAL, encoding="utf-8")
    health = {"campaign": "enderal", "data_root": str(tmp_path),
              "scene": {"setting": "city", "mood": "tense", "key": "brackenford.west-gate", "label": "West Gate",
                        "description": "x", "theme": [{"name": "Brackenford", "mood": "tense"}]}}
    payloads = _run_send(monkeypatch, ["--scene", "Saltmere, Meren's Bakery: warm loaves cooling"], health)
    cue = payloads[0]["music"]
    assert cue["event"] == "scene" and cue["setting"] == "city" and cue["mood"] == "warm"
    assert cue["scene"]["key"] == "saltmere.merens-bakery"
    assert cue["scene"]["era"] == "medieval"
    assert cue["scene"]["theme"] == [{"name": "Saltmere", "mood": "curious"}, {"name": "Meren’s Bakery", "mood": "warm"}]
    assert "saltmere.merens-bakery" in capsys.readouterr().out


def test_send_scene_same_place_sends_narration_without_a_cue(monkeypatch, capsys):
    current = derive("The Prancing Pony inn: songs")
    health = {"campaign": "", "scene": {"setting": current.setting, "mood": current.mood, **current.scene.to_dict()}}
    payloads = _run_send(monkeypatch, ["--scene", "Prancing Pony inn: the fire burns low"], health)
    assert payloads == [{"text": "The bell over the door rings."}]
    assert "unchanged" in capsys.readouterr().out


def test_send_scene_works_when_the_display_has_no_campaign_or_health(monkeypatch):
    payloads = _run_send(monkeypatch, ["--scene", "The ashen crypt: dust", "--mood", "tense"], None)
    assert payloads[0]["music"]["setting"] == "crypt" and payloads[0]["music"]["mood"] == "tense"


@pytest.mark.parametrize("args", [
    ["--scene", "Inn: songs", "--music-event", "scene", "--music-setting", "tavern"],
    ["--scene", "Inn: songs", "--scene-key", "inn"],
    ["--scene", "Inn: songs", "--music-mood", "warm"],
    ["--mood", "warm"],
    ["--scene", ""],
    ["--scene", "Inn", "--mood", "cheerful"],
])
def test_invalid_scene_shortcuts_fail_before_campaign_operations(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["send.py", *args])
    with mock.patch.object(send, "_ensure_active_campaign") as ensure:
        with pytest.raises(SystemExit):
            send.main()
        ensure.assert_not_called()


def test_send_scene_campaign_name_from_health_cannot_escape_the_data_root(tmp_path, monkeypatch):
    (tmp_path / "world.md").write_text("Era: modern.\n", encoding="utf-8")
    health = {"campaign": "..", "data_root": str(tmp_path), "scene": None}
    payloads = _run_send(monkeypatch, ["--scene", "Station: trains"], health)
    assert "era" not in payloads[0]["music"]["scene"]
