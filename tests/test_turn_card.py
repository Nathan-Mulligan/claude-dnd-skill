"""The per-turn card gives the DM a short, current cheat sheet built from the campaign files."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/enderal"
spec = importlib.util.spec_from_file_location("turn_card", REPO / "scripts/turn_card.py")
turn_card = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = turn_card
spec.loader.exec_module(turn_card)


@pytest.fixture
def enderal(tmp_path):
    folder = tmp_path / "campaigns" / "Enderal"
    shutil.copytree(FIXTURE, folder)
    (tmp_path / ".runtime").mkdir()
    return folder


def card(folder, party_input="[Elbane III]: We ask Meren about cargo."):
    return turn_card.build(folder, party_input)


def edit_state(folder, old, new):
    path = folder / "state.md"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_enderal_card_carries_the_scene_beat_hooks_clock_trade_and_people(enderal):
    text = card(enderal)
    assert text.startswith("[[Turn card, hidden from players: ") and text.endswith("]] ")
    assert "roll mode" not in text
    assert "\n" not in text
    assert "Where: Saltmere, Meren Vale’s bakery, 16 Duskfall 1247, morning" in text
    assert 'Beat 1b "Broken Ledgers": Bring the couple back toward Brackenford' in text
    assert "Dera Voss needs barrel hoops" in text and "Warden courier with a lame horse" in text
    assert "Mara quietly asks Elbane" not in text
    assert "auditor reaches the Brackenford tollhouse" in text and "[in 2 days]" in text
    assert "bridge closes to loaded wagons" not in text
    assert "Trade policy (apply it without asking): aim for a 20% margin" in text
    assert "People here: Meren Vale (Saltmere baker whose shop is by the market well)" in text
    assert "Mara Venn" not in text
    assert "rolls: players roll" in text
    for rule in ("never a bare \"what do you do?\"", "name the DC and the stake", "Never decide for the player characters"):
        assert rule in text
    assert 8 <= len(text.split(" | ")) <= 12
    assert len(text) < 2200


def test_table_settings_from_the_display_reach_the_dm(enderal):
    runtime = enderal.parent.parent / ".runtime"
    (runtime / "narration_target").write_text("120", encoding="utf-8")
    (runtime / "roll_prefs.json").write_text(json.dumps({"Nella Reed": "auto", "Ghost": "weird"}), encoding="utf-8")
    text = card(enderal)
    assert "narration about 120 words" in text
    assert text.endswith("]] [[Nella Reed roll mode: auto]] ")
    assert "Ghost" not in text


def test_clock_window_includes_overdue_and_skips_far_off(enderal):
    calendar = json.loads((enderal / "calendar.json").read_text(encoding="utf-8"))
    calendar["day"] = 20
    (enderal / "calendar.json").write_text(json.dumps(calendar), encoding="utf-8")
    text = card(enderal)
    assert "auditor reaches the Brackenford tollhouse" in text and "[overdue]" in text
    assert "bridge closes to loaded wagons" in text and "[in 1 day]" in text
    calendar["day"] = 1
    (enderal / "calendar.json").write_text(json.dumps(calendar), encoding="utf-8")
    assert "Clocks due" not in card(enderal)


def test_npc_named_in_the_party_input_is_listed_with_attitude(enderal):
    (enderal / "npcs.md").write_text(
        "| Name | Role | Attitude |\n|---|---|---|\n| Piers Lowell | Toll clerk | unfriendly |\n\n"
        "### Piers Lowell\n- **Demeanor:** jumpy, over-polite\n- **Attitude toward party:** unfriendly\n", encoding="utf-8")
    text = card(enderal, "[Nella Reed]: I ask Piers for the ledger.")
    assert "People here: Piers Lowell (jumpy, over-polite; unfriendly)" in text


def test_npc_saved_by_the_oracle_shows_temperament_and_want(enderal):
    oracle_spec = importlib.util.spec_from_file_location("oracle_for_card", REPO / "skills/dnd/scripts/oracle.py")
    oracle = importlib.util.module_from_spec(oracle_spec)
    oracle_spec.loader.exec_module(oracle)
    oracle.save_npc_fields(enderal / "npcs.md", "Meren Vale",
                           [("Temperament", "brisk, counts twice (ordinary)"), ("Wants now", "flour before the frost")])
    assert "People here: Meren Vale (brisk, counts twice (ordinary); wants flour before the frost)" in card(enderal)


def test_missing_sections_are_skipped(tmp_path):
    folder = tmp_path / "campaigns" / "Bare"
    folder.mkdir(parents=True)
    (folder / "state.md").write_text("# Campaign: Bare\n\n## Current Situation\n- **Location:** a ditch\n", encoding="utf-8")
    lines = card(folder).split(" | ")
    assert lines[0].endswith("Where: a ditch")
    assert len(lines) == 4
    assert all(word not in card(folder) for word in ("Beat", "Next hooks", "Clocks", "Trade", "People", "Table"))


def test_improvised_campaign_without_hooks_is_told_to_write_them(enderal):
    edit_state(enderal, "## Next Hooks\n", "## Old Hooks\n")
    assert "Next hooks: none written. Write two or three under ## Next Hooks" in card(enderal)


def test_structured_arc_names_the_next_key_beat_and_source(enderal):
    text = (enderal / "state.md").read_text(encoding="utf-8")
    start, end = text.index("```yaml"), text.index("```\n\n## Session Flags")
    structured = ("```yaml\ntype: structured\nsource: Keep\ncurrent_chapter: '1.2'\ncurrent_chapter_detail:\n"
                  "  id: '1.2'\n  title: The Caves\n  source_ref: source/1.2.md\n  key_beats: [Find the map, Meet the hermit]\n"
                  "outstanding_beats: [Meet the hermit]\nsteering_notes: The hermit sends smoke signals.\n")
    (enderal / "state.md").write_text(text[:start] + structured + text[end:], encoding="utf-8")
    out = card(enderal)
    assert 'Chapter 1.2 "The Caves": next key beat "Meet the hermit" (read source/1.2.md). Steering: The hermit sends smoke signals.' in out
    assert "none written" not in out


@pytest.mark.parametrize("damage", [
    lambda f: (f / "state.md").write_bytes(b"\xff\xfe broken \x00"),
    lambda f: (f / "calendar.json").write_text("{not json", encoding="utf-8"),
    lambda f: (f / "npcs.md").write_text("|||\n## \n", encoding="utf-8"),
    lambda f: (f / "state.md").write_text("## Campaign Arc\n```yaml\n: : bad\n```\n## Clocks\n- 99 Nonsense: x\n", encoding="utf-8"),
])
def test_broken_files_never_raise(enderal, damage):
    damage(enderal)
    assert isinstance(card(enderal), str)


def test_control_characters_never_reach_the_terminal(enderal):
    edit_state(enderal, "Saltmere, Meren", "Salt\x00mere\x1b[2J, Meren")
    text = card(enderal)
    assert "Salt mere [2J, Meren" in text
    assert not any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def test_no_state_means_no_card(tmp_path):
    assert card(tmp_path / "campaigns" / "Missing") == ""


def test_card_does_not_save_the_steering_count(enderal):
    for _ in range(6):
        card(enderal)
    assert not (enderal / "arc_pulse.json").exists()


def test_steering_nudge_takes_the_beat_line(enderal):
    (enderal / "arc_pulse.json").write_text(json.dumps({"beat": "1b", "turns": 3}), encoding="utf-8")
    text, pulse = turn_card.prepare(enderal, "[Elbane III]: We load the wagon.")
    assert pulse.directive and "DM steering, mandatory this turn" in text
    assert 'Beat 1b "Broken Ledgers":' not in text
