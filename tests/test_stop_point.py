"""Where play stopped: a short, bounded note a fresh DM reads first on resume."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/fenmoor"
SCRIPT = REPO / "skills/dnd/scripts/stop_point.py"


@pytest.fixture
def fenmoor(tmp_path):
    folder = tmp_path / "campaigns" / "Fenmoor"
    shutil.copytree(FIXTURE, folder)
    return folder


def stop_point(root, *args, stdin=""):
    env = dict(os.environ, DND_CAMPAIGN_ROOT=str(root))
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=20)


def exchanges(count):
    turns = []
    for number in range(1, count + 1):
        turns.append({"who": "Players", "text": f"Turn {number}: Wren asks Rosa about the ledger."})
        turns.append({"who": "DM", "text": f"Turn {number}: Rosa’s floury hands stop on the dough."})
    return turns


def test_wrap_up_keeps_the_last_exchanges_verbatim_and_the_resumed_dm_reads_them(fenmoor):
    root = fenmoor.parent.parent
    turns = exchanges(6) + [{"who": "DM", "text": "Rosa slides the ledger across. Wren, roll Insight."}]
    result = stop_point(root, "write", "--campaign", "Fenmoor",
                        "--where", "In Rosa’s bakery, Brinewick, morning of 16 Leaffall; Rosa has just shown the ledger.",
                        "--waiting", "Wren's Insight roll on Rosa (DC 13)", stdin=json.dumps(turns))
    assert result.returncode == 0, result.stderr
    text = (fenmoor / "session-tail.md").read_text(encoding="utf-8")
    assert text.startswith("# Where we stopped\n")
    assert "Rosa has just shown the ledger." in text
    assert "**Waiting on:** Wren's Insight roll on Rosa (DC 13)" in text
    assert "**DM:** Rosa slides the ledger across. Wren, roll Insight." in text
    assert "Rosa’s floury hands" in text
    assert text.split("## Last exchanges")[1].count("\n**") == 8
    assert "Turn 3: Wren" not in text and "Turn 3: Rosa" in text
    shown = stop_point(root, "show", "--campaign", "Fenmoor")
    assert shown.returncode == 0 and shown.stdout.strip() == text.strip()


def test_show_falls_back_to_the_display_tail_when_no_stop_point_was_written(fenmoor):
    root = fenmoor.parent.parent
    (fenmoor / "session-tail.md").write_text("# Old snapshot\n" + "x" * 50000, encoding="utf-8")
    tail = [{"text": f"Narration {n}"} for n in range(25)]
    tail += [{"player": "Wren Talley", "text": "I check the wagon."}, {"dice": True, "text": "d20 = 14"}]
    (fenmoor / "session_tail.json").write_text(json.dumps(tail), encoding="utf-8")
    shown = stop_point(root, "show", "--campaign", "Fenmoor")
    assert shown.returncode == 0
    assert "x" * 100 not in shown.stdout
    assert "**Wren Talley:** I check the wagon." in shown.stdout
    assert "**Dice:** d20 = 14" in shown.stdout
    assert "Narration 18" not in shown.stdout and "Narration 19" in shown.stdout


def test_show_without_any_record_says_so(fenmoor):
    shown = stop_point(fenmoor.parent.parent, "show", "--campaign", "Fenmoor")
    assert shown.returncode == 0
    assert "No stop point" in shown.stdout


@pytest.mark.parametrize("stdin, where", [
    ("not json", "At the bakery."),
    (json.dumps({"who": "DM", "text": "x"}), "At the bakery."),
    (json.dumps([{"who": "DM"}]), "At the bakery."),
    (json.dumps([{"who": "DM", "text": "x"}]), "  "),
])
def test_bad_input_leaves_the_previous_stop_point_alone(fenmoor, stdin, where):
    (fenmoor / "session-tail.md").write_text("# Where we stopped\nEarlier.\n", encoding="utf-8")
    result = stop_point(fenmoor.parent.parent, "write", "--campaign", "Fenmoor", "--where", where, stdin=stdin)
    assert result.returncode != 0
    assert (fenmoor / "session-tail.md").read_text(encoding="utf-8") == "# Where we stopped\nEarlier.\n"


def test_unknown_campaign_is_refused(tmp_path):
    result = stop_point(tmp_path, "show", "--campaign", "../escape")
    assert result.returncode != 0
