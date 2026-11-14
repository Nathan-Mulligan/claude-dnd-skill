"""push_stats.py --party reads every character sheet itself, so the DM never writes stats Python.

On 13 Nov a Sol DM spent 20 to 60 seconds of a turn writing Python that rebuilt the party's
stats payload from the sheets by hand. One flag now does it the same way every time.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests/fixtures/merchant_sheets"
DISPLAY = REPO / "skills/dnd/display"
sys.path.insert(0, str(REPO / "skills/dnd/scripts"))
sys.path.insert(0, str(DISPLAY))

import sheet_extract  # noqa: E402


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.delenv("DND_RUNTIME_DIR", raising=False)
    root = tmp_path / "campaigns" / "Duskmoor"
    (root / "characters").mkdir(parents=True)
    (root / "state.md").write_text("# Duskmoor\n", encoding="utf-8")
    for sheet in FIXTURES.glob("*.md"):
        shutil.copy(sheet, root / "characters" / sheet.name)
    return root


def _player(campaign, stem):
    return sheet_extract.player_payload(campaign, campaign / "characters" / f"{stem}.md")


def test_the_header_stats_come_from_the_sheet(campaign):
    wynn = _player(campaign, "wynn-harrow")
    assert wynn["name"] == "Wynn Harrow"
    assert (wynn["race"], wynn["class"], wynn["level"]) == ("Human", "Paladin", 1)
    assert wynn["xp"] == {"current": 0, "next": 300}
    assert wynn["hp"] == {"current": 12, "max": 12, "temp": 0}
    assert (wynn["ac"], wynn["initiative"], wynn["speed"]) == (15, "+1", 30)
    assert wynn["hit_dice"] == {"max": 1, "remaining": 1, "die": "d10"}
    assert wynn["ability_scores"]["str"] == {"score": 14, "mod": "+2"}
    assert wynn["ability_scores"]["int"] == {"score": 10, "mod": "+0"}
    assert wynn["spell_slots"] == {"1": {"used": 0, "max": 2}}
    assert wynn["inspiration"] is True


def test_the_sheet_view_carries_attacks_spells_features_and_inventory(campaign):
    sheet = _player(campaign, "wynn-harrow")["sheet"]
    assert sheet["attacks"][0] == {"name": "Longsword", "bonus": "+4", "damage": "1d8+2", "type": "slashing",
                                   "notes": "versatile 1d10; Sap mastery"}
    assert sheet["spells"]["prepared"] == ["Heroism", "Shield of Faith"]
    assert sheet["spells"]["cantrips"] == []
    assert (sheet["spells"]["save_dc"], sheet["spells"]["attack_bonus"]) == (12, "+4")
    assert any(f["name"] == "Lucky" for f in sheet["features"])
    assert sheet["inventory"]


def test_a_character_without_spells_sends_no_spell_keys(campaign):
    oswin = _player(campaign, "oswin-iv")
    assert "spell_slots" not in oswin and "spells" not in oswin["sheet"]
    assert oswin["hit_dice"]["die"] == "d8" and oswin["ac"] == 13


def test_blank_template_fields_are_left_out_not_guessed(campaign):
    path = campaign / "characters" / "blank.md"
    path.write_text((REPO / "skills/dnd/templates/character-sheet.md").read_text(encoding="utf-8")
                    .replace("# <Name>", "# Blank"), encoding="utf-8")
    blank = sheet_extract.player_payload(campaign, path)
    assert blank["name"] == "Blank"
    for key in ("level", "hp", "ac", "xp", "ability_scores", "spell_slots", "inspiration"):
        assert key not in blank, key


def test_push_stats_party_selects_the_campaign_and_merges_every_player(campaign, monkeypatch):
    import push_stats
    sent = []
    monkeypatch.setattr(push_stats, "_send", lambda url, data, token: sent.append(json.loads(data)))
    monkeypatch.setattr(sys, "argv", ["push_stats.py", "--party", "Duskmoor"])
    push_stats.main()
    payload = sent[0]
    assert payload["campaign"] == "Duskmoor"
    assert "replace_players" not in payload
    assert sorted(p["name"] for p in payload["players"]) == ["Oswin IV", "Wynn Harrow"]


def test_push_stats_party_can_replace_the_player_list(campaign, monkeypatch):
    import push_stats
    sent = []
    monkeypatch.setattr(push_stats, "_send", lambda url, data, token: sent.append(json.loads(data)))
    monkeypatch.setattr(sys, "argv", ["push_stats.py", "--party", "Duskmoor", "--replace-players"])
    push_stats.main()
    assert sent[0]["replace_players"] is True


def test_push_stats_party_reports_a_missing_campaign(campaign, monkeypatch, capsys):
    import push_stats
    monkeypatch.setattr(sys, "argv", ["push_stats.py", "--party", "Nowhere"])
    with pytest.raises(SystemExit):
        push_stats.main()
    assert "--party" in capsys.readouterr().err


def test_spell_lists_on_separate_lines_stay_separate():
    section = ("- **Cantrips:** Fire Bolt, Light\n- **Prepared:** Shield, Magic Missile\n"
               "- Spellcasting ability Intelligence; spell save DC 13, spell attack +5\n")
    spells = sheet_extract._spells(section)
    assert spells["cantrips"] == ["Fire Bolt", "Light"]
    assert spells["prepared"] == ["Shield", "Magic Missile"]
    assert (spells["save_dc"], spells["attack_bonus"]) == (13, "+5")


@pytest.mark.parametrize("written, held", [("yes", True), ("✓", True), ("1", True), ("no", False), ("0", False)])
def test_inspiration_written_as_a_word_or_a_tick_is_kept(campaign, written, held):
    path = campaign / "characters" / "wynn-harrow.md"
    text = path.read_text(encoding="utf-8")
    assert "**Heroic Inspiration:** 1" in text
    path.write_text(text.replace("**Heroic Inspiration:** 1", f"**Heroic Inspiration:** {written}"), encoding="utf-8")
    assert _player(campaign, "wynn-harrow")["inspiration"] is held


def test_a_mid_session_refresh_keeps_the_live_inspiration(campaign, monkeypatch):
    # send.py --inspiration-spend changes only the display; a refresh after a sheet change must not undo it.
    import push_stats
    sent = []
    monkeypatch.setattr(push_stats, "_send", lambda url, data, token: sent.append(json.loads(data)))
    monkeypatch.setattr(sys, "argv", ["push_stats.py", "--party", "Duskmoor"])
    push_stats.main()
    assert all("inspiration" not in p for p in sent[0]["players"])
    monkeypatch.setattr(sys, "argv", ["push_stats.py", "--party", "Duskmoor", "--replace-players"])
    push_stats.main()
    assert next(p for p in sent[1]["players"] if p["name"] == "Wynn Harrow")["inspiration"] is True
