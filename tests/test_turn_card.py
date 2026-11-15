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


def card(folder, party_input="[Corin Ashby]: We ask Effie about cargo."):
    return turn_card.build(folder, party_input)


def edit_state(folder, old, new):
    path = folder / "state.md"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_fenmoor_card_carries_the_scene_beat_hooks_clock_trade_and_people(fenmoor):
    text = card(fenmoor)
    assert text.startswith("[[Turn card, hidden from players: ") and text.endswith("]] ")
    assert "roll mode" not in text
    assert "\n" not in text
    assert "Where: Reedhythe, Effie Cobb’s candle shop, 9 Mistmonth 1312, morning" in text
    assert 'Beat 1b "Fish Oil and Smoke": Steer the cousins to Gullmere' in text
    assert "apprentice is buying every candle" in text and "eel barge has run onto the mudflats" in text
    assert "lend the cousins his fast skiff" not in text
    assert "eel barges try the night crossing" in text and "[in 2 days]" in text
    assert "fever house burns its last lamp oil" not in text
    assert "Trade policy (" in text and "aim for a 25% margin" in text
    assert "People here: Effie Cobb (Reedhythe candle-maker whose shop faces the fish quay)" in text
    assert "Maud Pellow" not in text
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
    calendar["day"] = 13
    (fenmoor / "calendar.json").write_text(json.dumps(calendar), encoding="utf-8")
    text = card(fenmoor)
    assert "eel barges try the night crossing" in text and "[overdue]" in text
    assert "fever house burns its last lamp oil" in text and "[in 1 day]" in text
    calendar["day"] = 1
    (fenmoor / "calendar.json").write_text(json.dumps(calendar), encoding="utf-8")
    assert "Clocks due" not in card(fenmoor)


def test_npc_named_in_the_party_input_is_listed_with_attitude(fenmoor):
    (fenmoor / "npcs.md").write_text(
        "| Name | Role | Attitude |\n|---|---|---|\n| Silas Crane | Oil factor | unfriendly |\n\n"
        "### Silas Crane\n- **Demeanor:** jumpy, over-polite\n- **Attitude toward party:** unfriendly\n", encoding="utf-8")
    text = card(fenmoor, "[Wren Talley]: I ask Silas for the manifest.")
    assert "People here: Silas Crane (jumpy, over-polite; unfriendly)" in text


def test_npc_is_matched_by_their_name_not_a_title_or_role_word(fenmoor):
    # #141: a first word such as "Captain" or "Warden" put the person on the card whenever it came up.
    (fenmoor / "npcs.md").write_text(
        "| Name | Role | Attitude |\n|---|---|---|\n| Captain Odell Marsh | Barge captain | wary |\n"
        "| Warden rider | Road patrol | curt |\n| Sister Yara | Shrine keeper | kind |\n", encoding="utf-8")
    for line in ("[Wren Talley]: I ask the Captain of the next barge for passage.",
                 "[Wren Talley]: Is the Warden post open today?",
                 "[Wren Talley]: My sister would love this town."):
        assert "People here" not in card(fenmoor, line), line
    assert "People here: Captain Odell Marsh (Barge captain; wary)" in card(fenmoor, "[Wren Talley]: We wave to Odell.")
    assert "People here: Captain Odell Marsh" in card(fenmoor, "[Wren Talley]: Captain Odell Marsh, a word?")
    assert "People here: Sister Yara (Shrine keeper; kind)" in card(fenmoor, "[Wren Talley]: I find Yara at the shrine.")
    assert "People here: Warden rider (Road patrol; curt)" in card(fenmoor, "[Wren Talley]: We hail the warden rider.")


def test_a_name_part_two_people_share_lists_neither(fenmoor):
    (fenmoor / "npcs.md").write_text(
        "| Name | Role |\n|---|---|\n| Captain Odell Marsh | Barge captain |\n| Odell Pike | Ferryman |\n", encoding="utf-8")
    assert "People here" not in card(fenmoor, "[Wren Talley]: We wave to Odell.")
    assert "People here: Odell Pike (Ferryman)" in card(fenmoor, "[Wren Talley]: We wave to Odell Pike.")


def test_a_given_name_that_is_another_persons_surname_lists_only_the_one_named(fenmoor):
    (fenmoor / "npcs.md").write_text(
        "| Name | Role |\n|---|---|\n| Captain Odell Marsh | Barge captain |\n| Captain Bram Odell | Dock guard |\n",
        encoding="utf-8")
    text = card(fenmoor, "[Wren Talley]: We wave to Captain Bram Odell.")
    assert "People here: Captain Bram Odell (Dock guard)" in text and "Marsh" not in text


def test_npc_saved_by_the_oracle_shows_temperament_and_want(fenmoor):
    oracle_spec = importlib.util.spec_from_file_location("oracle_for_card", REPO / "skills/dnd/scripts/oracle.py")
    oracle = importlib.util.module_from_spec(oracle_spec)
    oracle_spec.loader.exec_module(oracle)
    oracle.save_npc_fields(fenmoor / "npcs.md", "Effie Cobb",
                           [("Temperament", "brisk, counts twice (ordinary)"), ("Wants now", "beeswax before the fair")])
    assert "People here: Effie Cobb (brisk, counts twice (ordinary); wants beeswax before the fair)" in card(fenmoor)


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
    edit_state(fenmoor, "Reedhythe, Effie", "Reed\x00hythe\x1b[2J, Effie")
    text = card(fenmoor)
    assert "Reed hythe [2J, Effie" in text
    assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def test_no_state_means_no_card(tmp_path):
    assert card(tmp_path / "campaigns" / "Missing") == ""


def test_card_does_not_save_the_steering_count(fenmoor):
    for _ in range(6):
        card(fenmoor)
    assert not (fenmoor / "arc_pulse.json").exists()


def test_steering_nudge_takes_the_beat_line(fenmoor):
    (fenmoor / "arc_pulse.json").write_text(json.dumps({"beat": "1b", "turns": 3}), encoding="utf-8")
    text, pulse = turn_card.prepare(fenmoor, "[Corin Ashby]: We load the punt.")
    assert pulse.directive and "DM steering, mandatory this turn" in text
    assert 'Beat 1b "Fish Oil and Smoke":' not in text


def test_every_card_asks_for_narration_first_and_a_one_word_close(fenmoor):
    text = card(fenmoor)
    rule = next(part for part in text.split(" | ") if part.startswith("Rule: Turn shape"))
    assert "one send.py call with every block" in rule
    assert "bookkeeping in the next call" in rule
    assert "reply done" in rule


def test_the_trade_policy_shapes_a_trade_but_never_says_yes_for_the_party(fenmoor):
    line = next(p for p in card(fenmoor).split(" | ") if p.startswith("Trade policy"))
    assert "without asking" not in line
    assert "never agrees a price, fee or deal for them" in line
    assert "aim for a 25% margin" in line


def test_every_card_keeps_the_party_s_coin_and_word_theirs(fenmoor):
    rule = next(p for p in card(fenmoor).split(" | ") if p.startswith("Rule: Never decide for the player characters"))
    for words in ("spend their coin", "hand over their things", "toll", "bribe"):
        assert words in rule


def test_a_debt_or_offer_left_open_in_the_state_is_put_back_to_the_party(fenmoor):
    edit_state(fenmoor, "- Maud is at the Gullmere beacon",
               "- Ned offered 2 sp to tow his skiff back, not yet accepted. Effie sold them tea.\n"
               "- Maud is at the Gullmere beacon")
    line = next(p for p in card(fenmoor).split(" | ") if p.startswith("Waiting on the party"))
    assert "never pay, accept or hand over for them unasked" in line
    assert "The 2 sp mooring fee is still owed to the Reedhythe quay master before the punt leaves" in line
    assert "Ned offered 2 sp to tow his skiff back, not yet accepted" in line
    assert "share of the eel money was paid in full" not in line and "Effie sold them tea" not in line


def test_nothing_waiting_means_no_waiting_line(fenmoor):
    edit_state(fenmoor, " The 2 sp mooring fee is still owed to the Reedhythe quay master before the punt leaves.", "")
    edit_state(fenmoor, " The mooring fee remains due.", "")
    assert "Waiting on the party" not in card(fenmoor)


@pytest.mark.parametrize("settled", [
    "Ned offered 2 sp to tow his skiff back, and the party accepted.",
    "The 2 sp Ned was owed was paid in full.",
    "Ned offered 2 sp to tow his skiff back; they declined.",
])
def test_a_settled_debt_or_offer_is_not_put_back_to_the_party(fenmoor, settled):
    edit_state(fenmoor, " The 2 sp mooring fee is still owed to the Reedhythe quay master before the punt leaves.", "")
    edit_state(fenmoor, " The mooring fee remains due.", "")
    edit_state(fenmoor, "- Maud is at the Gullmere beacon", f"- {settled}\n- Maud is at the Gullmere beacon")
    assert "Waiting on the party" not in card(fenmoor)


def test_the_waiting_line_lets_this_turn_s_answer_settle_it(fenmoor):
    line = next(p for p in card(fenmoor, "We pay the 2 sp mooring fee now.").split(" | ")
                if p.startswith("Waiting on the party"))
    assert "unless this input answers it" in line


def _only(fenmoor, sentence):
    edit_state(fenmoor, " The 2 sp mooring fee is still owed to the Reedhythe quay master before the punt leaves.", "")
    edit_state(fenmoor, " The mooring fee remains due.", "")
    edit_state(fenmoor, "- Maud is at the Gullmere beacon", f"- {sentence}\n- Maud is at the Gullmere beacon")
    return next((p for p in card(fenmoor).split(" | ") if p.startswith("Waiting on the party")), "")


@pytest.mark.parametrize("sentence", [
    "Ned offered 2 sp to tow his skiff back, but the offer has not been accepted.",
    "The mooring fee is unpaid and has not been settled.",
    "Ned's eel money is paid; the mooring fee remains due.",
    "The 2 sp quay fee is due.",
    "Ned offers 2 sp to tow his skiff; the party has not answered.",
    "The 2 sp mooring fee remains due; the eel money is paid.",
    "The 2 sp mooring fee is owed and must be paid tomorrow.",
    "The agreed 2 sp fee is still owed.",
    "Ned offered 2 sp; the offer has not yet actually been accepted.",
])
def test_an_open_payment_or_offer_is_waiting_however_it_is_worded(fenmoor, sentence):
    assert sentence.rstrip(".") in _only(fenmoor, sentence)


@pytest.mark.parametrize("sentence", [
    "Ned offered 2 sp; the party agreed to tow the skiff.",
    "Effie offered condolences for their loss.",
    "No wages are owed to Wren.",
    "The night barge is due tomorrow.",
    "The party has 2 gp and the courier is due tomorrow.",
    "Effie offers help with the work.",
    "No wages are still owed to Wren.",
])
def test_an_agreed_deal_or_a_non_payment_is_not_waiting(fenmoor, sentence):
    assert _only(fenmoor, sentence) == ""
