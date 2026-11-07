"""The saved encounter owns turn order, attack specs and HP transitions."""

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills/dnd/scripts"
sys.path.insert(0, str(SCRIPTS))
import encounter_state as encounter  # noqa: E402


def campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    root = tmp_path / "campaigns" / "trial"
    (root / "characters").mkdir(parents=True)
    (root / "state.md").write_text(
        "# Trial\n\n## Pinned Facts\n- Keep the lantern lit.\n\n"
        "## Active Combat\nOld turn data\n\n## Live State Flags\n- Lantern: lit.\n"
    )
    (root / "session-log.md").write_text("# Session Log\n")
    (root / "characters" / "Mira.md").write_text("# Mira\n- **HP:** 12 / 12\n")
    return root


def combatants():
    return [
        {"name": "Goblin A", "type": "npc", "dex_mod": 2, "hp": 5,
         "ac": 15, "attack_bonus": 4, "damage": "1d6+2"},
        {"name": "Mira", "type": "pc", "dex_mod": 3, "hp": 12,
         "ac": 14, "attack_bonus": 5, "damage": "1d8+3"},
    ]


def fixed_order(actors):
    for actor in actors:
        actor["initiative_roll"] = 10
        actor["initiative"] = 10 + actor["dex_mod"]
    return actors


def test_attack_uses_frozen_spec_and_cannot_repeat_a_turn(tmp_path, monkeypatch):
    root = campaign(tmp_path, monkeypatch)
    monkeypatch.setattr(encounter, "initiative_order", fixed_order)
    calls = []

    def roll(bonus, ac, damage):
        calls.append((bonus, ac, damage))
        return {"d20": 18, "attack_bonus": bonus, "target_ac": ac,
                "total": 18 + bonus, "hit": True, "crit": False, "damage": 4}

    monkeypatch.setattr(encounter, "resolve_attack", roll)
    state = encounter.start("trial", combatants())
    assert state["current"] == "Goblin A"
    token = state["turn_token"]
    first = encounter._act("trial", token, "attack", "Mira")
    assert calls == [(4, 14, "1d6+2")]
    assert first["event"]["hp_before"] == 12
    assert first["event"]["hp_after"] == 8
    assert "**HP:** 8 / 12" in (root / "characters" / "Mira.md").read_text()
    assert first["next"]["current"] == "Mira"
    state_text = (root / "state.md").read_text()
    assert "**HP:** 8 / 12" in (root / "characters" / "Mira.md").read_text()
    assert "| Mira | pc | 8/12 | 14 |" in state_text
    assert "**Current turn:** 1:1:Mira" in state_text
    assert "Old turn data" not in state_text
    assert "- Keep the lantern lit." in state_text
    assert "- Lantern: lit." in state_text

    repeated = encounter._act("trial", token, "attack", "Mira")
    assert repeated["replayed"] is True
    assert len(calls) == 1
    with pytest.raises(ValueError, match="different action"):
        encounter._act("trial", token, "attack", "Goblin A")

    second = encounter._act("trial", first["next"]["turn_token"], "attack", "Goblin A")
    assert calls[-1] == (5, 15, "1d8+3")
    assert second["event"]["hp_after"] == 1
    assert second["next"]["current"] == "Goblin A"
    assert second["next"]["round"] == 2


def test_round_cap_pauses_before_next_round(tmp_path, monkeypatch):
    root = campaign(tmp_path, monkeypatch)
    monkeypatch.setattr(encounter, "initiative_order", fixed_order)
    first = encounter.start("trial", combatants(), max_rounds=1)
    second = encounter._act("trial", first["turn_token"], "pass")
    end = encounter._act("trial", second["next"]["turn_token"], "pass")
    assert end["next"]["status"] == "paused"
    assert end["next"]["round"] == 2
    assert end["next"]["turn_token"] is None
    state_text = (root / "state.md").read_text()
    log_text = (root / "session-log.md").read_text()
    assert "Round 1: Goblin A passed; Mira passed." in state_text
    assert "Round 1: Goblin A passed; Mira passed." in log_text
    assert state_text.count("[encounter:") == 1
    assert log_text.count("[encounter:") == 1
    assert encounter.checkpoint("trial") == []
    with pytest.raises(ValueError, match="wrong turn token"):
        encounter._act("trial", "2:0:Goblin A", "attack", "Mira")
    resumed = encounter.resume("trial", 2)
    assert resumed["status"] == "active"
    assert resumed["current"] == "Goblin A"
    assert "**Current turn:** 2:0:Goblin A" in (root / "state.md").read_text()


def test_rejects_bad_specs_and_ambiguous_sheet_before_start(tmp_path, monkeypatch):
    root = campaign(tmp_path, monkeypatch)
    wrong = combatants()
    wrong[0]["damage"] = "1d8+3+1d6"
    with pytest.raises(ValueError, match="one dice expression"):
        encounter.start("trial", wrong)
    assert not (root / "encounter.json").exists()
    (root / "characters" / "Mira.md").write_text("# Mira\nNo HP line\n")
    with pytest.raises(ValueError, match="ambiguous"):
        encounter.start("trial", combatants())
    assert not (root / "encounter.json").exists()
    (root / "characters" / "Mira.md").write_text("# Mira\n- **HP:** 8 / 12\n")
    with pytest.raises(ValueError, match="current HP differs"):
        encounter.start("trial", combatants())
    assert not (root / "encounter.json").exists()


def test_finished_encounter_archives_without_losing_roll_history(tmp_path, monkeypatch):
    root = campaign(tmp_path, monkeypatch)
    monkeypatch.setattr(encounter, "initiative_order", fixed_order)
    monkeypatch.setattr(encounter, "resolve_attack", lambda bonus, ac, damage: {
        "d20": 20, "attack_bonus": bonus, "target_ac": ac, "total": 20 + bonus,
        "hit": True, "crit": True, "damage": 5,
    })
    first = encounter.start("trial", combatants())
    second = encounter._act("trial", first["turn_token"], "pass")
    done = encounter._act("trial", second["next"]["turn_token"], "attack", "Goblin A")
    assert done["next"]["status"] == "finished"
    archive = encounter.archive("trial")
    assert archive == root / "encounters" / "encounter-0001.json"
    assert not (root / "encounter.json").exists()
    assert '"hp_after": 0' in archive.read_text()
    assert "Goblin A" in (root / "session-log.md").read_text()
    next_encounter = encounter.start("trial", combatants())
    assert next_encounter["status"] == "active"


def test_same_side_attack_requires_explicit_override(tmp_path, monkeypatch):
    root = campaign(tmp_path, monkeypatch)
    (root / "characters" / "Aldric.md").write_text("# Aldric\n- **HP:** 16 / 16\n")
    actors = combatants() + [{"name": "Aldric", "type": "pc", "dex_mod": 1,
                             "hp": 16, "ac": 16, "attack_bonus": 5, "damage": "1d8+3"}]
    monkeypatch.setattr(encounter, "initiative_order", fixed_order)
    first = encounter.start("trial", actors)
    mira_turn = encounter._act("trial", first["turn_token"], "pass")["next"]
    with pytest.raises(ValueError, match="same-side"):
        encounter._act("trial", mira_turn["turn_token"], "attack", "Aldric")
    assert len(encounter._load("trial")["events"]) == 1


def test_corrupt_state_fails_without_replacing_it(tmp_path, monkeypatch):
    root = campaign(tmp_path, monkeypatch)
    path = root / "encounter.json"
    path.write_text('{"version":1,"campaign":"trial","combatants":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid encounter state"):
        encounter._load("trial")
    assert path.read_text() == '{"version":1,"campaign":"trial","combatants":[]}'


def test_fixed_multiattack_uses_each_profile_once_and_then_advances(tmp_path, monkeypatch):
    root = campaign(tmp_path, monkeypatch)
    monkeypatch.setattr(encounter, "initiative_order", fixed_order)
    actors = combatants()
    actors[0].pop("attack_bonus")
    actors[0].pop("damage")
    actors[0]["attacks"] = [
        {"name": "Claw", "attack_bonus": 4, "damage": "1d6+2"},
        {"name": "Bite", "attack_bonus": 3, "damage": "1d4+1"},
    ]
    calls = []

    def roll(bonus, ac, damage):
        calls.append((bonus, ac, damage))
        return {"d20": 18, "attack_bonus": bonus, "target_ac": ac,
                "total": 18 + bonus, "hit": True, "crit": False, "damage": 2}

    monkeypatch.setattr(encounter, "resolve_attack", roll)
    first = encounter.start("trial", actors)
    assert first["turn_token"] == "1:0:Goblin A:1"
    one = encounter._act("trial", first["turn_token"], "attack", "Mira")
    assert one["event"]["attack_name"] == "Claw"
    assert one["next"]["current"] == "Goblin A"
    assert one["next"]["turn_token"] == "1:0:Goblin A:2"
    assert encounter._act("trial", first["turn_token"], "attack", "Mira")["replayed"]
    two = encounter._act("trial", one["next"]["turn_token"], "attack", "Mira")
    assert two["event"]["attack_name"] == "Bite"
    assert two["next"]["current"] == "Mira"
    assert two["next"]["turn_token"] == "1:1:Mira"
    assert calls == [(4, 14, "1d6+2"), (3, 14, "1d4+1")]
    assert "**HP:** 8 / 12" in (root / "characters" / "Mira.md").read_text()
    assert "**Current attack:** Bite (2/2)" not in (root / "state.md").read_text()


def test_multiattack_pass_skips_remaining_strikes(tmp_path, monkeypatch):
    campaign(tmp_path, monkeypatch)
    monkeypatch.setattr(encounter, "initiative_order", fixed_order)
    actors = combatants()
    actors[0]["attacks"] = [
        {"name": "Claw", "attack_bonus": 4, "damage": "1d6+2"},
        {"name": "Bite", "attack_bonus": 3, "damage": "1d4+1"},
    ]
    with pytest.raises(ValueError, match="cannot mix"):
        encounter.start("trial", actors)
    actors[0].pop("attack_bonus")
    actors[0].pop("damage")
    first = encounter.start("trial", actors)
    skipped = encounter._act("trial", first["turn_token"], "pass")
    assert skipped["next"]["current"] == "Mira"
    assert len(skipped["next"]["events"]) == 1


def test_multiattack_ends_when_last_enemy_falls_on_first_strike(tmp_path, monkeypatch):
    campaign(tmp_path, monkeypatch)
    monkeypatch.setattr(encounter, "initiative_order", fixed_order)
    actors = combatants()
    actors[0].pop("attack_bonus")
    actors[0].pop("damage")
    actors[0]["attacks"] = [
        {"name": "Claw", "attack_bonus": 4, "damage": "1d6+2"},
        {"name": "Bite", "attack_bonus": 3, "damage": "1d4+1"},
    ]
    monkeypatch.setattr(encounter, "resolve_attack", lambda *_: {
        "d20": 20, "attack_bonus": 4, "target_ac": 14, "total": 24,
        "hit": True, "crit": True, "damage": 12,
    })
    first = encounter.start("trial", actors)
    result = encounter._act("trial", first["turn_token"], "attack", "Mira")
    assert result["next"]["status"] == "finished"
    assert result["next"]["turn_token"] is None
    assert len(result["next"]["events"]) == 1


def test_negative_damage_never_heals_a_target(tmp_path, monkeypatch):
    campaign(tmp_path, monkeypatch)
    monkeypatch.setattr(encounter, "initiative_order", fixed_order)
    monkeypatch.setattr(encounter, "resolve_attack", lambda *_: {
        "d20": 18, "attack_bonus": 4, "target_ac": 14, "total": 22,
        "hit": True, "crit": False, "damage": -1,
    })
    first = encounter.start("trial", combatants())
    result = encounter._act("trial", first["turn_token"], "attack", "Mira")
    assert result["event"]["hp_before"] == 12
    assert result["event"]["hp_after"] == 12


def test_multiattack_round_cap_waits_until_all_strikes_finish(tmp_path, monkeypatch):
    campaign(tmp_path, monkeypatch)
    monkeypatch.setattr(encounter, "initiative_order", fixed_order)
    actors = combatants()
    actors[0].pop("attack_bonus")
    actors[0].pop("damage")
    actors[0]["attacks"] = [
        {"name": "Claw", "attack_bonus": 4, "damage": "1d6+2"},
        {"name": "Bite", "attack_bonus": 3, "damage": "1d4+1"},
    ]
    first = encounter.start("trial", actors, max_rounds=1)
    one = encounter._act("trial", first["turn_token"], "pass")
    assert one["next"]["current"] == "Mira"
    end = encounter._act("trial", one["next"]["turn_token"], "pass")
    assert end["next"]["status"] == "paused"
    assert end["next"]["round"] == 2
    resumed = encounter.resume("trial", 2)
    assert resumed["turn_token"] == "2:0:Goblin A:1"
