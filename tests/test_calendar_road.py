"""calendar.py advance rolls the road for each travel day in improvised campaigns."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "dnd" / "scripts"


def state(kind):
    return f"# Campaign\n\n## Campaign Arc\n```yaml\ntype: {kind}\n```\n\n## Session Flags\n- pacing: mixed\n"


@pytest.fixture
def root(tmp_path):
    return tmp_path


def make(root, name, kind):
    folder = root / "campaigns" / name
    folder.mkdir(parents=True)
    (folder / "state.md").write_text(state(kind), encoding="utf-8")
    run(root, name, "init", "--date", "15 Duskfall 1247", "--months", "Harvestmoon,Duskfall")
    return folder


def run(root, name, *args):
    env = {**os.environ, "DND_CAMPAIGN_ROOT": str(root)}
    result = subprocess.run([sys.executable, str(SCRIPTS / "calendar.py"), "-c", name, *args],
                            capture_output=True, text=True, encoding="utf-8", env=env)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("kind", ["dynamic", "sandbox"])
def test_travel_days_bring_one_road_roll_each(root, kind):
    folder = make(root, "Improv", kind)
    out = run(root, "Improv", "advance", "3", "days")
    assert "18 Duskfall 1247" in out
    for day in (1, 2, 3):
        assert f"Day {day} of 3" in out
    assert "16 Duskfall 1247" in out and "17 Duskfall 1247" in out
    assert "--no-road" in out
    assert len(json.loads((folder / "road_log.json").read_text(encoding="utf-8"))) == 3


def test_weeks_roll_every_day(root):
    make(root, "Improv", "dynamic")
    assert "Day 7 of 7" in run(root, "Improv", "advance", "1", "week")


def test_prebuilt_campaigns_get_no_road_rolls(root):
    folder = make(root, "Module", "structured")
    out = run(root, "Module", "advance", "3", "days")
    assert "On the road" not in out and "--no-road" not in out
    assert not (folder / "road_log.json").exists()


def test_no_road_skips_the_rolls(root):
    folder = make(root, "Improv", "dynamic")
    out = run(root, "Improv", "advance", "2", "days", "--no-road")
    assert "17 Duskfall 1247" in out
    assert "On the road" not in out
    assert not (folder / "road_log.json").exists()


def test_hours_short_of_a_day_and_rests_roll_nothing(root):
    make(root, "Improv", "dynamic")
    assert "On the road" not in run(root, "Improv", "advance", "8", "hours")
    assert "On the road" not in run(root, "Improv", "rest", "long")
    assert "Day 1 of 1" in run(root, "Improv", "advance", "30", "hours")


def test_campaign_without_state_still_advances(root):
    (root / "campaigns" / "Bare").mkdir(parents=True)
    run(root, "Bare", "init", "--date", "1 Duskfall 1247", "--months", "Duskfall")
    out = run(root, "Bare", "advance", "2", "days")
    assert "3 Duskfall 1247" in out and "On the road" not in out
