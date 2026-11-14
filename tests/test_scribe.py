"""The scribe writes the turn log in the background, off the DM's clock.

The timing report found the DM's per-turn bookkeeping call (session-log and state.md writes) cost
8 to 10 seconds a turn on Terra and 21 to 34 on Sol. The scribe takes the per-turn log off the DM.
It is the only writer of turn-log.md and never touches state.md.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
DISPLAY = REPO / "skills/dnd/display"
sys.path.insert(0, str(DISPLAY))

import scribe  # noqa: E402

STATE = "# Campaign: Duskmoor\n\n## Recent Events\n- arrived\n"


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


def lines(camp):
    return [l for l in (camp / "turn-log.md").read_text(encoding="utf-8").splitlines() if l.lstrip().startswith("- ")]


def fake_model(prompt, n):
    return [f"line {i}" for i in range(n)]


def test_record_queues_what_the_table_heard_and_starts_a_worker(camp):
    with mock.patch.object(scribe.subprocess, "Popen") as popen:
        scribe.record(camp, turn("We take it.", "Edrin hands over the satchel."))
    rows = scribe._read_queue()
    assert rows[0]["campaign_dir"] == str(camp) and rows[0]["starts_turn"] is True
    assert rows[0]["blocks"][1] == ["narration", "Edrin hands over the satchel."]
    assert popen.call_args.kwargs["start_new_session"] is True


def test_a_roll_and_its_result_are_one_turn_and_a_new_player_line_starts_the_next(camp):
    queue(camp, turn("Nella haggles.", "DC 13; fail and the price holds."), turn(None, "Fourteen. Hessa sighs."),
          turn("We leave.", "The road east is empty."))
    seen = {}

    def model(prompt, n):
        seen["prompt"], seen["n"] = prompt, n
        return fake_model(prompt, n)

    with mock.patch.object(scribe, "summarise", side_effect=model), mock.patch.object(scribe, "kick"):
        assert scribe.run(settle_s=0) == "written 2"
    assert seen["n"] == 2 and "Fourteen. Hessa sighs." in seen["prompt"]
    assert [l.split(" ", 2)[2] for l in lines(camp)] == ["line 0", "line 1"]
    assert scribe._read_queue() == []


def test_the_scribe_never_writes_state_md(camp):
    queue(camp, turn("Go.", "On."))
    before = (camp / "state.md").stat().st_mtime_ns
    with mock.patch.object(scribe, "summarise", side_effect=fake_model), mock.patch.object(scribe, "kick"):
        scribe.run(settle_s=0)
    assert (camp / "state.md").read_text(encoding="utf-8") == STATE
    assert (camp / "state.md").stat().st_mtime_ns == before


def test_a_result_that_arrives_after_its_turn_was_logged_is_marked_as_a_continuation(camp):
    queue(camp, turn("Nella haggles.", "DC 13."))
    with mock.patch.object(scribe, "summarise", side_effect=fake_model), mock.patch.object(scribe, "kick"):
        scribe.run(settle_s=0)
        queue(camp, turn(None, "Seventeen. Hessa gives way."))
        scribe.run(settle_s=0)
    text = (camp / "turn-log.md").read_text(encoding="utf-8")
    assert text.count("## Session from") == 1
    assert "  - " in text and "(continued) line 0" in text


def test_a_save_starts_a_new_session_heading(camp):
    queue(camp, turn("Go.", "On."))
    with mock.patch.object(scribe, "summarise", side_effect=fake_model), mock.patch.object(scribe, "kick"):
        scribe.run(settle_s=0)
        (camp / "session-tail.md").write_text("saved", encoding="utf-8")
        future = time.time() + 5
        os.utime(camp / "session-tail.md", (future, future))
        with mock.patch.object(scribe.time, "time", return_value=future + 5):
            queue(camp, turn("Again.", "Onward."))
        scribe.run(settle_s=0)
    assert (camp / "turn-log.md").read_text(encoding="utf-8").count("## Session from") == 2


def test_a_failed_model_still_logs_the_turn_plainly_and_says_why(camp):
    queue(camp, turn("We wait.", "Rain on the tollhouse roof. A clerk calls Nella's name."))
    with mock.patch.object(scribe, "summarise", side_effect=RuntimeError("codex exited 1")), \
         mock.patch.object(scribe, "kick"):
        assert scribe.run(settle_s=0) == "written 1"
    assert "Rain on the tollhouse roof." in (camp / "turn-log.md").read_text(encoding="utf-8")
    assert "codex exited 1" in Path(scribe.log_path()).read_text(encoding="utf-8")


def test_a_crash_while_writing_keeps_the_turns_queued(camp):
    queue(camp, turn("Go.", "On."))
    with mock.patch.object(scribe, "summarise", side_effect=fake_model), \
         mock.patch.object(scribe, "_append_log", side_effect=OSError("disk full")), pytest.raises(OSError):
        scribe.run(settle_s=0)
    assert len(scribe._read_queue()) == 1


def test_a_turn_queued_while_the_worker_runs_is_kept_for_the_next_pass(camp):
    queue(camp, turn("Go.", "On."))

    def model(prompt, n):
        queue(camp, turn("Then.", "Later."))
        return fake_model(prompt, n)

    with mock.patch.object(scribe, "summarise", side_effect=model), mock.patch.object(scribe, "kick"):
        scribe.run(settle_s=0)
    assert len(lines(camp)) >= 2


@pytest.mark.skipif(scribe.fcntl is None, reason="flock is POSIX only")
def test_a_second_worker_while_one_holds_the_lock_leaves_the_queue_alone(camp):
    queue(camp, turn("Go.", "On."))
    with scribe._locked("scribe.lock") as held:
        assert held
        assert scribe.run(settle_s=0) == "busy"
    assert len(scribe._read_queue()) == 1


def test_switched_off_records_nothing(camp, monkeypatch):
    monkeypatch.setenv("DND_SCRIBE", "0")
    with mock.patch.object(scribe.subprocess, "Popen") as popen:
        scribe.record(camp, turn("Go.", "On."))
    assert scribe._read_queue() == [] and not popen.called


def _sender(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_send_scribe_test", DISPLAY / "send.py")
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path))
    sender = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sender)
    return sender


def test_send_hands_each_delivered_narration_call_to_the_scribe(tmp_path, monkeypatch):
    import io
    sender = _sender(tmp_path, monkeypatch)
    camp_dir = tmp_path / "campaigns" / "Duskmoor"
    recorded = []
    monkeypatch.setattr(sys, "argv", ["send.py"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("@player Everybody\nWe go.\n@narration\nThe gate opens.\n"))
    monkeypatch.setattr(sender, "_post", lambda *a: True)
    monkeypatch.setattr(sender, "_ensure_active_campaign", lambda: camp_dir)
    monkeypatch.setattr(sender.scribe, "record", lambda folder, blocks: recorded.append((folder, blocks)))
    sender.main()
    assert recorded == [(camp_dir, [["player Everybody", "We go."], ["narration", "The gate opens."]])]


def test_a_block_the_display_never_got_is_not_logged(tmp_path, monkeypatch):
    import io
    sender = _sender(tmp_path, monkeypatch)
    recorded, results = [], iter([True, False])
    monkeypatch.setattr(sys, "argv", ["send.py"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("@player Everybody\nWe go.\n@narration\nThe gate opens.\n"))
    monkeypatch.setattr(sender, "_post", lambda *a: next(results))
    monkeypatch.setattr(sender, "_ensure_active_campaign", lambda: tmp_path)
    monkeypatch.setattr(sender.scribe, "record", lambda folder, blocks: recorded.append(blocks))
    sender.main()
    assert recorded == [[["player Everybody", "We go."]]]
