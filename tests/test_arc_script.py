"""arc.py moves and reshapes the campaign arc in state.md without disturbing the rest of the file."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills/dnd/scripts/arc.py"
spec = importlib.util.spec_from_file_location("arc_script", SCRIPT)
arc = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = arc
spec.loader.exec_module(arc)

STATE = (REPO / "tests/fixtures/enderal/state.md").read_text(encoding="utf-8")


def outside_arc(text):
    start = text.index("```yaml")
    return text[:start], text[text.index("```", start + 3):]


def test_advance_completes_the_beat_and_moves_act_and_outstanding():
    data, message = arc.advance(arc.read(STATE))
    beats = {str(b["id"]): b for _, b in arc.beats(data)}
    assert beats["1b"]["status"] == "complete"
    assert beats["2a"]["status"] == "current"
    assert data["current_beat"] == "2a" and data["current_act"] == 2
    assert data["outstanding_beats"] == ["2a", "2b", "3a", "3b"]
    assert data["steering_notes"] == ""
    assert "2a" in message and "--steering" in message


def test_advance_round_trip_keeps_the_rest_of_state_untouched():
    updated = arc.write(STATE, arc.advance(arc.read(STATE))[0])
    assert outside_arc(updated) == outside_arc(STATE)
    reread = arc.read(updated)
    original = arc.read(STATE)
    for key in ("theme", "resolution", "arc_number", "revision_log"):
        assert reread[key] == original[key]
    assert arc.current(reread)["label"] == "Who Pays"


def test_advancing_a_later_beat_keeps_the_current_one():
    data, _ = arc.advance(arc.read(STATE), "2a")
    assert data["current_beat"] == "1b"
    assert "2a" not in data["outstanding_beats"]


def test_advancing_the_last_beat_completes_the_arc():
    data = arc.read(STATE)
    for _ in range(5):
        data, message = arc.advance(data)
    assert data["current_beat"] is None and data["outstanding_beats"] == []
    assert "arc is complete" in message
    with pytest.raises(arc.ArcError):
        arc.advance(data)


def test_revise_round_trip_changes_only_the_named_fields():
    data = arc.revise(arc.read(STATE), "1b", pressure="Piers flees with the ledger", steering="Let Mara ask for help",
                      today="2026-11-13")
    updated = arc.write(STATE, data)
    assert outside_arc(updated) == outside_arc(STATE)
    beat = arc.current(arc.read(updated))
    assert beat["world_pressure"] == "Piers flees with the ledger"
    assert beat["what_changes"] == "The disruption proves to have interests beyond one gate"
    assert arc.read(updated)["steering_notes"] == "Let Mara ask for help"
    assert arc.read(updated)["revision_log"][-1] == "2026-11-13: 1b revised (pressure, steering)"


@pytest.mark.parametrize("beat,kwargs", [("1a", {"pressure": "x"}), ("9z", {"pressure": "x"}), ("1b", {})])
def test_revise_refuses_completed_unknown_or_empty(beat, kwargs):
    with pytest.raises(arc.ArcError):
        arc.revise(arc.read(STATE), beat, **kwargs)


def test_crlf_files_stay_crlf():
    crlf = STATE.replace("\n", "\r\n")
    updated = arc.write(crlf, arc.advance(arc.read(crlf))[0])
    assert "\n" not in updated.replace("\r\n", "")
    assert arc.current(arc.read(updated))["id"] == "2a"


def test_structured_advance_ticks_off_the_next_key_beat():
    data = {"type": "structured", "current_chapter_detail": {"id": "1.1", "title": "Gate", "key_beats": ["A", "B"],
                                                              "source_ref": "source/1.1.md"},
            "outstanding_beats": ["A", "B", "Z"]}
    assert arc.structured_next(data)["beat"] == "A"
    data, message = arc.advance(data)
    assert data["outstanding_beats"] == ["B", "Z"] and "Next key beat: B" in message
    data, message = arc.advance(data)
    assert "read arc.md" in message and arc.structured_next(data) is None


def run_cli(tmp_path, *args):
    env = dict(os.environ, DND_CAMPAIGN_ROOT=str(tmp_path), PYTHONUTF8="1")
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8", env=env)


def test_cli_status_advance_and_revise(tmp_path):
    folder = tmp_path / "campaigns" / "Enderal"
    folder.mkdir(parents=True)
    path = folder / "state.md"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(STATE)
    status = run_cli(tmp_path, "status", "--campaign", "Enderal")
    assert status.returncode == 0 and 'beat 1b "Broken Ledgers"' in status.stdout
    assert run_cli(tmp_path, "advance", "--campaign", "Enderal").returncode == 0
    assert arc.read(path.read_text(encoding="utf-8"))["current_beat"] == "2a"
    refused = run_cli(tmp_path, "revise", "--campaign", "Enderal", "--beat", "1b", "--pressure", "x")
    assert refused.returncode == 1 and "complete" in refused.stderr
    assert run_cli(tmp_path, "revise", "--campaign", "Enderal", "--beat", "2a", "--steering", "Rivals bid").returncode == 0
    assert arc.read(path.read_text(encoding="utf-8"))["steering_notes"] == "Rivals bid"
    assert not list(folder.glob("*.arc-tmp"))
