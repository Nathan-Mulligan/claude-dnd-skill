"""The DM gets a steering nudge when the story sits on one beat for too many party turns."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("arc_pulse", REPO / "scripts/arc_pulse.py")
arc_pulse = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = arc_pulse
spec.loader.exec_module(arc_pulse)

STATE = """# Campaign: Fenmoor

## Campaign Arc
```yaml
type: dynamic
acts:
  - act: 1
    title: The Narrow Road
    beats:
      - {{id: 1a, label: The Cost of Passage, what_changes: Corin's trade becomes entangled in the shortage, world_pressure: Ilsa enforces the posted rate while Garrick seeks salt, status: current}}
      - {{id: 1b, label: Broken Ledgers, what_changes: The disruption has interests beyond one gate, world_pressure: The Wardens and Compact dispute records, status: pending}}
current_act: 1
current_beat: {beat}
```

## Session Flags
roll_mode: auto
{flags}

## DM Style Notes
Heroic danger.
"""


def campaign(tmp_path, beat="1a", flags=""):
    folder = tmp_path / "campaigns" / "Fenmoor"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.md").write_text(STATE.format(beat=beat, flags=flags), encoding="utf-8")
    return folder


def play_turns(folder, count):
    directives = []
    for _ in range(count):
        pulse = arc_pulse.plan(folder)
        arc_pulse.commit(folder, pulse)
        directives.append(pulse.directive)
    return directives


def test_reads_the_current_beat_from_flow_style_yaml(tmp_path):
    beat = arc_pulse.arc.current(arc_pulse.arc.read((campaign(tmp_path) / "state.md").read_text(encoding="utf-8")))
    assert beat["id"] == "1a" and beat["label"] == "The Cost of Passage"
    assert "Ilsa enforces" in beat["world_pressure"]


def test_nudges_on_the_sixth_turn_by_default_then_counts_again(tmp_path):
    directives = play_turns(campaign(tmp_path), 12)
    assert [i for i, d in enumerate(directives, 1) if d] == [6, 12]
    assert "Ilsa enforces the posted rate" in directives[5]
    assert "Has this beat already landed?" in directives[11]
    assert "arc.py advance --campaign Fenmoor" in directives[11]
    assert "arc.py revise --campaign Fenmoor --beat 1a" in directives[11]


def test_dm_led_spotlight_nudges_sooner(tmp_path):
    directives = play_turns(campaign(tmp_path, flags="spotlight: dm_led"), 4)
    assert directives[-1] and not any(directives[:-1])


def test_steer_after_overrides_and_zero_turns_it_off(tmp_path):
    assert play_turns(campaign(tmp_path, flags="spotlight: dm_led\nsteer_after: 2"), 2)[-1]
    assert not any(play_turns(campaign(tmp_path / "off", flags="steer_after: 0"), 12))


def test_advancing_the_beat_resets_the_count(tmp_path):
    folder = campaign(tmp_path)
    play_turns(folder, 5)
    campaign(tmp_path, beat="1b")
    directives = play_turns(folder, 5)
    assert not any(directives)
    saved = json.loads((folder / "arc_pulse.json").read_text(encoding="utf-8"))
    assert saved["beat"] == "1b" and saved["turns"] == 5 and saved["nudges"] == 0


def test_sandbox_or_missing_arc_never_steers(tmp_path):
    folder = tmp_path / "campaigns" / "Free"
    folder.mkdir(parents=True)
    (folder / "state.md").write_text("## Campaign Arc\n```yaml\ntype: sandbox\n```\n", encoding="utf-8")
    assert arc_pulse.plan(folder) is None
    assert arc_pulse.plan(tmp_path / "campaigns" / "Missing") is None


portal_spec = importlib.util.spec_from_file_location("campaign_portal_for_pulse", REPO / "scripts/campaign_portal.py")
portal = importlib.util.module_from_spec(portal_spec)
portal_spec.loader.exec_module(portal)


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(portal, "RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(portal, "META_FILE", tmp_path / ".runtime" / "portal-session.json")
    monkeypatch.setattr(portal, "running", lambda: True)
    monkeypatch.setattr(portal, "notify_party_consumed", lambda *args: None)
    portal.RUNTIME.mkdir(parents=True)
    portal.META_FILE.write_text('{"campaign":"Fenmoor","mode":"resume"}', encoding="utf-8")
    sent = []
    result = {"code": 0}

    def fake_run(*args, **kwargs):
        if args[:2] == ("tmux", "load-buffer"):
            sent.append(Path(args[-1]).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(args, result["code"], "", "")

    monkeypatch.setattr(portal, "run", fake_run)
    return campaign(tmp_path, flags="steer_after: 2"), sent, result


def reply(text="[Corin Ashby]: We haggle over the flour."):
    (portal.RUNTIME / ".input_queue").write_text(text, encoding="utf-8")
    return portal.deliver_party_input()


def test_portal_adds_the_nudge_to_the_stalled_turn(delivery):
    folder, sent, _ = delivery
    assert reply()
    assert "DM steering" not in sent[-1]
    assert reply()
    assert "DM steering" in sent[-1] and "We haggle over the flour" in sent[-1]


def test_failed_delivery_does_not_count_a_turn(delivery):
    folder, sent, result = delivery
    result["code"] = 1
    assert not reply()
    assert not (folder / "arc_pulse.json").exists()


@pytest.mark.parametrize("arc", ["acts: [unfinished]", "acts: 1", "acts:\n  - beats: oops", "- just a list"])
def test_malformed_arc_never_blocks_steering(tmp_path, arc):
    folder = tmp_path / "campaigns" / "Bad"
    folder.mkdir(parents=True)
    (folder / "state.md").write_text(f"## Campaign Arc\n```yaml\ntype: dynamic\ncurrent_beat: 1a\n{arc}\n```\n", encoding="utf-8")
    assert arc_pulse.plan(folder) is None


def test_reply_still_reaches_the_dm_when_steering_breaks(delivery, monkeypatch):
    folder, sent, _ = delivery
    monkeypatch.setattr(portal.turn_card.arc_pulse, "plan", lambda folder, text="": 1 / 0)
    assert reply()
    assert "We haggle over the flour" in sent[-1]


def test_steering_notes_location_and_hooks_lead_the_directive(tmp_path):
    folder = campaign(tmp_path, flags="steer_after: 1")
    state = folder / "state.md"
    text = state.read_text(encoding="utf-8").replace(
        "current_act: 1\n", "current_act: 1\nsteering_notes: A clerk asks them to carry a sealed ledger.\n")
    text = ("## Current Situation\n- **Location:** Brinewick market\n\n## Next Hooks\n- A courier with a lame horse\n\n"
            + text)
    state.write_text(text, encoding="utf-8")
    directive = arc_pulse.plan(folder).directive
    assert "A clerk asks them to carry a sealed ledger" in directive
    assert "at Brinewick market" in directive and "A courier with a lame horse" in directive
    assert "Ilsa enforces" not in directive


def test_world_pressure_is_the_fallback(tmp_path):
    directive = arc_pulse.plan(campaign(tmp_path, flags="steer_after: 1")).directive
    assert "Ilsa enforces the posted rate" in directive and "entangled in the shortage" in directive


def test_flag_lines_written_as_list_items_are_read(tmp_path):
    directives = play_turns(campaign(tmp_path, flags="- spotlight: dm_led"), 4)
    assert directives[-1] and not any(directives[:-1])
    assert arc_pulse.turn_limit("## Session Flags\n- steer_after: 2\n") == 2


def test_active_combat_turns_are_not_counted(tmp_path):
    folder = campaign(tmp_path, flags="steer_after: 2")
    state = folder / "state.md"
    clean_text = state.read_text(encoding="utf-8")
    state.write_text(clean_text + "\n## Active Combat\nRound 2: Corin vs bandit (HP 4/7)\n", encoding="utf-8")
    assert arc_pulse.plan(folder) is None
    for ended in ("None.", "*(none)*", "", "**Status:** Finished in round 3\n**Current turn:** none\n\n| Combatant | Side | HP | AC |"):
        state.write_text(clean_text + f"\n## Active Combat\n{ended}\n", encoding="utf-8")
        assert arc_pulse.plan(folder) is not None


@pytest.mark.parametrize("text", [
    "[Corin Ashby]: 14", "[Corin Ashby]: I rolled a 17 for Perception", "[Wren Talley]: 8 and 9",
    "[Wren Talley]: nat 20", "[Corin Ashby]: d20: 12 + 4 = 16",
    "Corin Ashby rolls 1d20+4: [13] +4 = 17 — Inspect rope: Perception",
    "[Everybody]: OOC: back in five minutes", "[Wren Talley]: (ooc) can we stop at nine?",
    "[Corin Ashby]: ((pausing for tea))", "[Corin Ashby]: // what time is it",
    "[Corin Ashby]: 15\n[Wren Talley]: OOC: nice roll",
])
def test_dice_only_and_out_of_character_inputs_do_not_count(tmp_path, text):
    assert not arc_pulse.counts_as_story(text)
    assert arc_pulse.plan(campaign(tmp_path), text) is None


@pytest.mark.parametrize("text", [
    "[Corin Ashby]: We haggle over the flour.", "[Corin Ashby]: I rolled a 14\n[Wren Talley]: I grab the ledger",
    "[Corin Ashby]: I offer 20 silver for the wagon", "[Corin Ashby]: 20 for the wagon", "",
])
def test_story_inputs_count(text):
    assert arc_pulse.counts_as_story(text)


def test_revising_the_beat_restarts_the_count_and_the_nudge_ladder(tmp_path):
    folder = campaign(tmp_path, flags="steer_after: 2")
    assert play_turns(folder, 4)[3].startswith("DM steering, mandatory this turn: beat 1a")
    state = folder / "state.md"
    state.write_text(state.read_text(encoding="utf-8").replace("Ilsa enforces the posted rate", "Ilsa seizes a wagon"),
                     encoding="utf-8")
    directives = play_turns(folder, 2)
    assert directives[0] is None
    assert "Ilsa seizes a wagon" in directives[1] and "already landed" not in directives[1]


def test_structured_arc_gets_a_nudge_naming_the_key_beat_and_source(tmp_path):
    folder = tmp_path / "campaigns" / "Keep"
    folder.mkdir(parents=True)
    (folder / "state.md").write_text(
        "## Campaign Arc\n```yaml\ntype: structured\ncurrent_chapter: '1.1'\ncurrent_chapter_detail:\n  id: '1.1'\n"
        "  title: The Gate\n  source_ref: source/1.1.md\n  key_beats: [Find the map, Meet the hermit]\n"
        "outstanding_beats: [Find the map, Meet the hermit]\n```\n\n## Session Flags\nsteer_after: 2\n", encoding="utf-8")
    directives = play_turns(folder, 4)
    assert directives[1] and '"Find the map"' in directives[1] and "source/1.1.md" in directives[1]
    assert "arc.py advance --campaign Keep" in directives[3]


def test_braces_in_campaign_text_do_not_break_the_nudge(tmp_path):
    folder = campaign(tmp_path, flags="steer_after: 1")
    state = folder / "state.md"
    state.write_text(state.read_text(encoding="utf-8").replace(
        "current_act: 1\n", "current_act: 1\nsteering_notes: Deliver the note marked {M} and {turns}.\n"), encoding="utf-8")
    directive = arc_pulse.plan(folder).directive
    assert "Deliver the note marked {M} and {turns}" in directive and "for 1 party turns" in directive
