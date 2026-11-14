"""The per-turn card gives the DM a short, current cheat sheet built from the campaign files."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/fenmoor"
spec = importlib.util.spec_from_file_location("turn_card", REPO / "scripts/turn_card.py")
turn_card = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = turn_card
spec.loader.exec_module(turn_card)


@pytest.fixture
def fenmoor(tmp_path):
    folder = tmp_path / "campaigns" / "Fenmoor"
    shutil.copytree(FIXTURE, folder)
    (tmp_path / ".runtime").mkdir()
    return folder


def card(folder, party_input="[Corin Ashby]: We ask Rosa about cargo."):
    return turn_card.build(folder, party_input)


def edit_state(folder, old, new):
    path = folder / "state.md"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_enderal_card_carries_the_scene_beat_hooks_clock_trade_and_people(fenmoor):
    text = card(fenmoor)
    assert text.startswith("[[Turn card, hidden from players: ") and text.endswith("]] ")
    assert "roll mode" not in text
    assert "\n" not in text
    assert "Where: Brinewick, Rosa Whitlow’s bakery, 16 Leaffall 1247, morning" in text
    assert 'Beat 1b "Broken Ledgers": Bring the couple back toward Ashbridge' in text
    assert "Tamsin Cole needs barrel hoops" in text and "Warden courier with a lame horse" in text
    assert "Ilsa quietly asks Corin" not in text
    assert "auditor reaches the Ashbridge tollhouse" in text and "[in 2 days]" in text
    assert "bridge closes to loaded wagons" not in text
    assert "Trade policy (" in text and "aim for a 20% margin" in text
    assert "People here: Rosa Whitlow (Brinewick baker whose shop is by the market well)" in text
    assert "Ilsa Brook" not in text
    assert "rolls: players roll" in text
    for rule in ("never a bare \"what do you do?\"", "name the DC and the stake", "Never decide for the player characters"):
        assert rule in text
    assert 8 <= len(text.split(" | ")) <= 12
    assert len(text) < 2200


def test_table_settings_from_the_display_reach_the_dm(fenmoor):
    runtime = fenmoor.parent.parent / ".runtime"
    (runtime / "narration_target").write_text("120", encoding="utf-8")
    (runtime / "roll_prefs.json").write_text(json.dumps({"Wren Talley": "auto", "Ghost": "weird"}), encoding="utf-8")
    text = card(fenmoor)
    assert "narration about 120 words" in text
    assert text.endswith("]] [[Wren Talley roll mode: auto]] ")
    assert "Ghost" not in text


def test_clock_window_includes_overdue_and_skips_far_off(fenmoor):
    calendar = json.loads((fenmoor / "calendar.json").read_text(encoding="utf-8"))
    calendar["day"] = 20
    (fenmoor / "calendar.json").write_text(json.dumps(calendar), encoding="utf-8")
    text = card(fenmoor)
    assert "auditor reaches the Ashbridge tollhouse" in text and "[overdue]" in text
    assert "bridge closes to loaded wagons" in text and "[in 1 day]" in text
    calendar["day"] = 1
    (fenmoor / "calendar.json").write_text(json.dumps(calendar), encoding="utf-8")
    assert "Clocks due" not in card(fenmoor)


def test_npc_named_in_the_party_input_is_listed_with_attitude(fenmoor):
    (fenmoor / "npcs.md").write_text(
        "| Name | Role | Attitude |\n|---|---|---|\n| Bram Tully | Toll clerk | unfriendly |\n\n"
        "### Bram Tully\n- **Demeanor:** jumpy, over-polite\n- **Attitude toward party:** unfriendly\n", encoding="utf-8")
    text = card(fenmoor, "[Wren Talley]: I ask Bram for the ledger.")
    assert "People here: Bram Tully (jumpy, over-polite; unfriendly)" in text


def test_npc_saved_by_the_oracle_shows_temperament_and_want(fenmoor):
    oracle_spec = importlib.util.spec_from_file_location("oracle_for_card", REPO / "skills/dnd/scripts/oracle.py")
    oracle = importlib.util.module_from_spec(oracle_spec)
    oracle_spec.loader.exec_module(oracle)
    oracle.save_npc_fields(fenmoor / "npcs.md", "Rosa Whitlow",
                           [("Temperament", "brisk, counts twice (ordinary)"), ("Wants now", "flour before the frost")])
    assert "People here: Rosa Whitlow (brisk, counts twice (ordinary); wants flour before the frost)" in card(fenmoor)


def test_missing_sections_are_skipped(tmp_path):
    folder = tmp_path / "campaigns" / "Bare"
    folder.mkdir(parents=True)
    (folder / "state.md").write_text("# Campaign: Bare\n\n## Current Situation\n- **Location:** a ditch\n", encoding="utf-8")
    lines = card(folder).split(" | ")
    assert lines[0].endswith("Where: a ditch")
    assert len(lines) == 5
    assert all(word not in card(folder) for word in ("Beat", "Next hooks", "Clocks", "Trade", "People", "Table"))


def test_improvised_campaign_without_hooks_is_told_to_write_them(fenmoor):
    edit_state(fenmoor, "## Next Hooks\n", "## Old Hooks\n")
    assert "Next hooks: none written. Write two or three under ## Next Hooks" in card(fenmoor)


def test_structured_arc_names_the_next_key_beat_and_source(fenmoor):
    text = (fenmoor / "state.md").read_text(encoding="utf-8")
    start, end = text.index("```yaml"), text.index("```\n\n## Session Flags")
    structured = ("```yaml\ntype: structured\nsource: Keep\ncurrent_chapter: '1.2'\ncurrent_chapter_detail:\n"
                  "  id: '1.2'\n  title: The Caves\n  source_ref: source/1.2.md\n  key_beats: [Find the map, Meet the hermit]\n"
                  "outstanding_beats: [Meet the hermit]\nsteering_notes: The hermit sends smoke signals.\n")
    (fenmoor / "state.md").write_text(text[:start] + structured + text[end:], encoding="utf-8")
    out = card(fenmoor)
    assert 'Chapter 1.2 "The Caves": next key beat "Meet the hermit" (read source/1.2.md). Steering: The hermit sends smoke signals.' in out
    assert "none written" not in out


@pytest.mark.parametrize("damage", [
    lambda f: (f / "state.md").write_bytes(b"\xff\xfe broken \x00"),
    lambda f: (f / "calendar.json").write_text("{not json", encoding="utf-8"),
    lambda f: (f / "npcs.md").write_text("|||\n## \n", encoding="utf-8"),
    lambda f: (f / "state.md").write_text("## Campaign Arc\n```yaml\n: : bad\n```\n## Clocks\n- 99 Nonsense: x\n", encoding="utf-8"),
])
def test_broken_files_never_raise(fenmoor, damage):
    damage(fenmoor)
    assert isinstance(card(fenmoor), str)


def test_control_characters_never_reach_the_terminal(fenmoor):
    edit_state(fenmoor, "Brinewick, Rosa", "Salt\x00mere\x1b[2J, Rosa")
    text = card(fenmoor)
    assert "Salt mere [2J, Rosa" in text
    assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def test_no_state_means_no_card(tmp_path):
    assert card(tmp_path / "campaigns" / "Missing") == ""


def test_card_does_not_save_the_steering_count(fenmoor):
    for _ in range(6):
        card(fenmoor)
    assert not (fenmoor / "arc_pulse.json").exists()


def test_steering_nudge_takes_the_beat_line(fenmoor):
    (fenmoor / "arc_pulse.json").write_text(json.dumps({"beat": "1b", "turns": 3}), encoding="utf-8")
    text, pulse = turn_card.prepare(fenmoor, "[Corin Ashby]: We load the wagon.")
    assert pulse.directive and "DM steering, mandatory this turn" in text
    assert 'Beat 1b "Broken Ledgers":' not in text


def test_every_card_asks_for_narration_first_and_a_one_word_close(fenmoor):
    text = card(fenmoor)
    rule = next(part for part in text.split(" | ") if part.startswith("Rule: Turn shape"))
    assert "one send.py call with every block" in rule
    assert "bookkeeping in the next call" in rule
    assert "reply done" in rule


@pytest.mark.parametrize("said,situation", [
    ("We push on toward Brackenford before dark.", "travel"),
    ("Corin draws his sword and charges the bandit.", "combat"),
    ("Ilsa tries to haggle the price of the flour down.", "trade"),
])
def test_the_card_names_the_situation_file_to_read(fenmoor, said, situation):
    text = card(fenmoor, said)
    line = next(part for part in text.split(" | ") if part.startswith("Situation"))
    assert f"skills/dnd/play/{situation}.md" in line
    assert "once" in line


def test_an_ordinary_turn_names_no_situation_file(fenmoor):
    assert "Situation" not in card(fenmoor, "Corin asks Rosa how her daughter is.")


def test_an_open_fight_in_the_state_names_the_combat_file(fenmoor):
    edit_state(fenmoor, "## Active Combat\nNone.", "## Active Combat\n- round 2, Corin vs two bandits")
    assert "skills/dnd/play/combat.md" in card(fenmoor, "Ilsa keeps her head down.")


def test_every_named_situation_file_exists():
    for name in turn_card.SITUATIONS:
        assert (REPO / "skills/dnd/play" / f"{name}.md").is_file(), name


def test_a_fight_on_the_road_names_only_the_combat_file(fenmoor):
    line = next(p for p in card(fenmoor, "On the road two men step out; Corin draws his dagger and stabs the nearer one.")
                .split(" | ") if p.startswith("Situation"))
    assert "combat.md" in line and "travel.md" not in line


@pytest.mark.parametrize("said,situation", [
    ("How much do you charge for a room?", "trade"),
    ("We strike a bargain with the miller.", None),
    ("We head to the market and look around.", "travel"),
    ("Corin charges at the bandit.", "combat"),
])
def test_the_card_does_not_mistake_ordinary_words_for_a_fight(fenmoor, said, situation):
    line = next((p for p in card(fenmoor, said).split(" | ") if p.startswith("Situation")), "")
    assert (situation is None and not line) or f"play/{situation}.md" in line


@pytest.mark.parametrize("combat", ["No active combat.", "- None", "*(none)*",
                                    "**Status:** Finished in round 3. Bandits fled."])
def test_a_closed_fight_in_the_state_is_not_an_open_one(fenmoor, combat):
    edit_state(fenmoor, "## Active Combat\nNone.", f"## Active Combat\n{combat}")
    assert "combat.md" not in card(fenmoor, "Corin asks Rosa how her daughter is.")


def test_the_trade_policy_shapes_a_trade_but_never_says_yes_for_the_party(fenmoor):
    line = next(p for p in card(fenmoor).split(" | ") if p.startswith("Trade policy"))
    assert "without asking" not in line
    assert "never agrees a price, fee or deal for them" in line
    assert "aim for a 20% margin" in line


def test_every_card_keeps_the_party_s_coin_and_word_theirs(fenmoor):
    rule = next(p for p in card(fenmoor).split(" | ") if p.startswith("Rule: Never decide for the player characters"))
    for words in ("spend their coin", "hand over their things", "toll", "bribe"):
        assert words in rule


def test_a_debt_or_offer_left_open_in_the_state_is_put_back_to_the_party(fenmoor):
    edit_state(fenmoor, "- Ilsa is at the Ashbridge west gate",
               "- Garrick offered 2 sp to haul his flour back, not yet accepted. Rosa sold them bread.\n"
               "- Ilsa is at the Ashbridge west gate")
    line = next(p for p in card(fenmoor).split(" | ") if p.startswith("Waiting on the party"))
    assert "never pay, accept or hand over for them" in line
    assert "The 3 sp wagon and mule rental is still owed to its owner at the next market" in line
    assert "Garrick offered 2 sp to haul his flour back, not yet accepted" in line
    assert "salt debt is paid in full" not in line and "Rosa sold them bread" not in line


def test_nothing_waiting_means_no_waiting_line(fenmoor):
    edit_state(fenmoor, " The 3 sp wagon and mule rental is still owed to its owner at the next market.", "")
    edit_state(fenmoor, "; the wagon rental remains due", "")
    assert "Waiting on the party" not in card(fenmoor)
