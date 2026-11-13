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

STATE = """# Campaign: Enderal

## Campaign Arc
```yaml
type: dynamic
acts:
  - act: 1
    title: The Narrow Road
    beats:
      - {{id: 1a, label: The Cost of Passage, what_changes: Elbane's trade becomes entangled in the shortage, world_pressure: Mara enforces the posted rate while Tovin seeks salt, status: current}}
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
    folder = tmp_path / "campaigns" / "Enderal"
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
    beat = arc_pulse.current_beat((campaign(tmp_path) / "state.md").read_text(encoding="utf-8"))
    assert beat.id == "1a" and beat.label == "The Cost of Passage"
    assert "Mara enforces" in beat.world_pressure


def test_nudges_on_the_sixth_turn_by_default_then_counts_again(tmp_path):
    directives = play_turns(campaign(tmp_path), 12)
    assert [i for i, d in enumerate(directives, 1) if d] == [6, 12]
    assert "Mara enforces the posted rate" in directives[5]
    assert "/dm:dnd arc advance" in directives[5]


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
    assert json.loads((folder / "arc_pulse.json").read_text(encoding="utf-8")) == {"beat": "1b", "turns": 5}


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
    portal.META_FILE.write_text('{"campaign":"Enderal","mode":"resume"}', encoding="utf-8")
    sent = []
    result = {"code": 0}
    monkeypatch.setattr(portal, "run", lambda *args, **kwargs: (
        sent.append(" ".join(args)), subprocess.CompletedProcess(args, result["code"], "", ""))[1])
    return campaign(tmp_path, flags="steer_after: 2"), sent, result


def reply(text="[Elbane III]: We haggle over the flour."):
    (portal.RUNTIME / ".input_queue").write_text(text, encoding="utf-8")
    return portal.deliver_party_input()


def test_portal_adds_the_nudge_to_the_stalled_turn(delivery):
    folder, sent, _ = delivery
    assert reply()
    assert "DM steering" not in sent[-2]
    assert reply()
    assert "DM steering" in sent[-2] and "We haggle over the flour" in sent[-2]


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
    monkeypatch.setattr(portal.arc_pulse, "plan", lambda folder: 1 / 0)
    assert reply()
    assert "We haggle over the flour" in sent[-2]
