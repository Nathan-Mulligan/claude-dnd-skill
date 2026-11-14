"""Old campaigns can repair NPC voices that clash.

Before automatic casting, voices were picked by hand and collided: one
campaign had a player character and two NPCs on the same voice. Casting
treats those older entries as hand picks and never touches them, so a one-off
repair re-casts only the clashing NPCs: player characters and the narrator
keep their voices, the NPC cast first keeps its voice, and the others get an
unused voice of the same gender, or a same-gender Kokoro blend once the plain
voices run out.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

DISPLAY = Path(__file__).resolve().parent.parent / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))
cast_voices = importlib.import_module("cast_voices")
tts = importlib.import_module("tts")

MALE, FEMALE = tts.LOCAL_VOICES_MALE, tts.LOCAL_VOICES_FEMALE

# The shape of the campaign that prompted this: three on bf_emma (one a PC),
# two on af_bella, two on am_onyx.
CLASHING = {
    "elbane iii": "bm_lewis",
    "mara venn": "bf_alice",
    "tovin pell": "bm_george",
    "hessa marr": "af_bella",
    "nella reed": "bf_emma",
    "bera holt": "af_bella",
    "jessa pike": "bf_emma",
    "orla fen": "af_sky",
    "dera voss": "af_heart",
    "meren vale": "bf_emma",
    "man at the barrier": "am_onyx",
    "disarmed bandit": "am_onyx",
    "edrin shaw": "am_fenrir",
}
PARTY = ["Elbane III", "Nella Reed"]


def _repair(raw=None, party=PARTY, narrator="bm_fable"):
    return cast_voices.repair(dict(raw or CLASHING), narrator=narrator, party=party,
                              pool=MALE + FEMALE, male=MALE, female=FEMALE)


def test_every_speaker_ends_up_with_a_different_voice():
    cast, _, _ = _repair()
    assert len(set(cast.values())) == len(cast)
    assert "bm_fable" not in cast.values()


def test_only_the_clashing_npcs_change():
    cast, src, changes = _repair()
    changed = {name for name, _, _ in changes}
    assert changed == {"bera holt", "jessa pike", "meren vale", "disarmed bandit"}
    for name, voice in CLASHING.items():
        if name not in changed:
            assert cast[name] == voice, name
    assert all(src[name] == cast_voices.AUTO for name in changed)


def test_player_characters_keep_their_voice_even_when_cast_later():
    raw = {"jessa pike": "bf_emma", "nella reed": "bf_emma"}
    cast, _, changes = _repair(raw)
    assert cast["nella reed"] == "bf_emma"
    assert [c[0] for c in changes] == ["jessa pike"]


def test_an_npc_on_the_narrator_voice_is_moved():
    cast, _, changes = _repair({"old tam": "bm_fable"})
    assert cast["old tam"] != "bm_fable"
    assert changes[0][0] == "old tam"


def test_new_voices_keep_the_gender_and_use_blends_when_plain_ones_run_out():
    cast, _, changes = _repair()
    for name, old, new in changes:
        assert cast_voices.voice_gender(new) == cast_voices.voice_gender(old), (name, new)
    # every plain female voice is held by someone who keeps it, so the
    # three moved women all need blends
    women = [new for name, old, new in changes if old.startswith(("af_", "bf_"))]
    assert all("+" in v for v in women)


def test_no_blend_borrows_the_narrator_or_a_player_character():
    _, _, changes = _repair()
    for name, _, new in changes:
        parts = set(new.split("+"))
        assert not parts & {"bm_fable", "bm_lewis", "bf_emma"}, (name, new)


def test_a_cast_without_clashes_is_left_alone():
    _, _, changes = _repair({"mara venn": "bf_alice", "tovin pell": "bm_george"})
    assert changes == []


def test_the_command_only_writes_with_write_and_keeps_a_backup(tmp_path, capsys):
    camp = tmp_path / "Enderal"
    (camp / "characters").mkdir(parents=True)
    (camp / "characters" / "nella-reed.md").write_text("# Nella Reed\n", encoding="utf-8")
    (camp / "characters" / "elbane-iii.md").write_text("# Elbane III\n", encoding="utf-8")
    (camp / "state.md").write_text("## Session Flags\ntts_voice: bm_fable\n", encoding="utf-8")
    original = json.dumps(CLASHING, indent=2)
    (camp / "cast.json").write_text(original, encoding="utf-8")

    assert cast_voices.main(["repair", str(camp)]) == 0
    assert (camp / "cast.json").read_text(encoding="utf-8") == original
    assert "Dry run" in capsys.readouterr().out

    assert cast_voices.main(["repair", str(camp), "--write"]) == 0
    saved = json.loads((camp / "cast.json").read_text(encoding="utf-8"))
    voices = cast_voices.voices(saved)
    assert len(set(voices.values())) == len(voices)
    assert voices["nella reed"] == "bf_emma"
    assert len(list(camp.glob("cast.json.before-repair-*"))) == 1

    assert cast_voices.main(["repair", str(camp), "--write"]) == 0
    assert "Nothing to change" in capsys.readouterr().out.splitlines()[-1]
