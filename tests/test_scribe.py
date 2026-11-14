"""The scribe writes the turn log and Recent Events in the background, off the DM's clock.

The timing report found the DM's per-turn bookkeeping call (session-log and state.md writes) cost
8 to 10 seconds a turn on Terra and 21 to 34 on Sol. The scribe takes the log off the DM.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
DISPLAY = REPO / "skills/dnd/display"
sys.path.insert(0, str(DISPLAY))

import scribe  # noqa: E402

STATE = """# Campaign: Duskmoor

## Current Situation
- **Location:** Brackenford road

## Recent Events
*(Session 1 pending)*

## Active Combat
None.
"""


@pytest.fixture
def camp(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path / "runtime"))
    (tmp_path / "runtime").mkdir()
    folder = tmp_path / "campaigns" / "Duskmoor"
    folder.mkdir(parents=True)
    (folder / "state.md").write_text(STATE, encoding="utf-8")
    return folder


def turn(player, *narration):
    blocks = [["player Everybody", player]] if player else []
    return blocks + [["narration", text] for text in narration]


def queue(camp, *turns):
    with mock.patch.object(scribe, "kick"):
        for blocks in turns:
            scribe.record(camp, blocks)


def test_record_queues_what_the_table_heard_and_starts_a_run(camp):
    with mock.patch.object(scribe.subprocess, "Popen") as popen:
        scribe.record(camp, turn("We take it.", "Edrin hands over the satchel."))
    rows = [json.loads(line) for line in Path(scribe.queue_path()).read_text(encoding="utf-8").splitlines()]
    assert rows[0]["campaign_dir"] == str(camp)
    assert rows[0]["blocks"][1] == ["narration", "Edrin hands over the satchel."]
    assert popen.call_args.kwargs["start_new_session"] is True


def test_a_roll_and_its_result_are_one_turn_and_a_new_player_line_starts_the_next(camp):
    queue(camp, turn("Nella haggles.", "DC 13; fail and the price holds."), turn(None, "Fourteen. Hessa sighs."),
          turn("We leave.", "The road east is empty."))
    seen = {}

    def fake(prompt, n):
        seen["prompt"], seen["n"] = prompt, n
        return [f"line {i}" for i in range(n)]

    with mock.patch.object(scribe, "summarise", side_effect=fake):
        assert scribe.run(settle_s=0) == "written 2"
    assert seen["n"] == 2
    assert "Fourteen. Hessa sighs." in seen["prompt"]
    log = (camp / "turn-log.md").read_text(encoding="utf-8")
    assert log.count("- ") >= 2 and "line 0" in log and "line 1" in log
    state = (camp / "state.md").read_text(encoding="utf-8")
    recent = state.split("## Recent Events\n", 1)[1].split("\n## ", 1)[0]
    assert recent.strip().splitlines() == ["- line 0", "- line 1"]
    assert "## Active Combat\nNone." in state and not Path(scribe.queue_path()).exists()


def test_recent_events_keeps_only_the_newest_bullets(camp):
    for i in range(scribe.RECENT_MAX + 3):
        queue(camp, turn("Go.", f"Step {i}."))
        with mock.patch.object(scribe, "summarise", side_effect=lambda prompt, n, i=i: [f"event {i}"]):
            scribe.run(settle_s=0)
    recent = (camp / "state.md").read_text(encoding="utf-8").split("## Recent Events\n", 1)[1].split("\n## ", 1)[0]
    bullets = recent.strip().splitlines()
    assert len(bullets) == scribe.RECENT_MAX and bullets[-1] == f"- event {scribe.RECENT_MAX + 2}"


def test_a_failed_model_still_logs_the_turn_plainly_and_says_why(camp):
    queue(camp, turn("We wait.", "Rain on the tollhouse roof. A clerk calls Nella's name."))
    with mock.patch.object(scribe, "summarise", side_effect=RuntimeError("codex exited 1")):
        assert scribe.run(settle_s=0) == "written 1"
    log = (camp / "turn-log.md").read_text(encoding="utf-8")
    assert "Rain on the tollhouse roof." in log
    assert "codex exited 1" in Path(scribe.log_path()).read_text(encoding="utf-8")


def test_a_dm_write_during_the_edit_is_kept(camp):
    path = camp / "state.md"
    calls = []

    def edit(text):
        if not calls:
            path.write_text(text.replace("Brackenford road", "Brackenford tollhouse"), encoding="utf-8")
            os.utime(path, ns=(1, 1))
        calls.append(text)
        return text.replace("*(Session 1 pending)*", "- arrived")

    assert scribe.edit_file(path, edit) is True
    final = path.read_text(encoding="utf-8")
    assert "Brackenford tollhouse" in final and "- arrived" in final and len(calls) == 2


def test_a_second_run_while_one_is_busy_leaves_work_pending(camp):
    queue(camp, turn("Go.", "On."))
    fd = scribe._acquire_lock()
    try:
        with mock.patch.object(scribe, "kick"):
            assert scribe.run(settle_s=0) == "busy"
        assert Path(scribe.queue_path()).exists()
    finally:
        scribe._release(fd)


def test_switched_off_records_nothing(camp, monkeypatch):
    monkeypatch.setenv("DND_SCRIBE", "0")
    with mock.patch.object(scribe.subprocess, "Popen") as popen:
        scribe.record(camp, turn("Go.", "On."))
    assert not Path(scribe.queue_path()).exists() and not popen.called


def test_send_hands_each_narration_call_to_the_scribe(tmp_path, monkeypatch):
    import importlib.util
    import io
    spec = importlib.util.spec_from_file_location("_send_scribe_test", DISPLAY / "send.py")
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path))
    sender = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sender)
    camp_dir = tmp_path / "campaigns" / "Duskmoor"
    recorded = []
    monkeypatch.setattr(sys, "argv", ["send.py"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("@player Everybody\nWe go.\n@narration\nThe gate opens.\n"))
    monkeypatch.setattr(sender, "_post", lambda *a: True)
    monkeypatch.setattr(sender, "_ensure_active_campaign", lambda: camp_dir)
    monkeypatch.setattr(sender.scribe, "record", lambda folder, blocks: recorded.append((folder, blocks)))
    sender.main()
    assert recorded == [(camp_dir, [["player Everybody", "We go."], ["narration", "The gate opens."]])]
