"""Road days serve the story: mostly quiet or tied to the beat, hooks, clocks and errand, with few random hazards."""
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import oracle  # noqa: E402

HAZARDS = {"obstacle", "discovery", "danger"}
MONTHS = ["Frostmonth", "Rainmonth", "Sunmonth"]

STATE = """# Campaign

## Active Quests
- Carry the vintner's three casks to the abbey cellar at Fernhollow and bring back the cellarer's seal.

## Next Hooks
- Fernhollow: the abbey cellarer is short of wine for the feast and will pay well for speed.
- The ferry crossing: the ferryman is overcharging and the reeve wants to know why.

## Clocks
- 12 Rainmonth 900: the abbey feast begins and the cellar is sealed (seen by: pilgrims hurrying the same way)

## Campaign Arc
```yaml
type: dynamic
acts:
- act: 1
  beats:
  - id: 1a
    label: Short Measures
    what_changes: The party learns the reeve skims the ferry tolls
    world_pressure: The reeve's men lean on every carter to pay twice at the ferry.
    status: current
current_beat: 1a
```

## Session Flags
- pacing: PACING
"""


def state(pacing="adventure"):
    return STATE.replace("PACING", pacing)


def context(pacing="adventure", due=None):
    ctx = oracle.read_road_context(state(pacing))
    for clock in ctx["clocks"]:
        clock["due"] = due
    return ctx


def tie(day):
    """How a day reads at the table: quiet, tied to the story, or an unrelated interruption."""
    if day["kind"] == "quiet":
        return "quiet"
    if day["kind"] == "story" or day.get("source") or day.get("stakes"):
        return "story"
    return "unrelated"


def journeys(pacing, days=2, n=3000, due=None, first_day=None):
    ctx = context(pacing, due)
    return [oracle.roll_road(days, rng=random.Random(seed), context=ctx, first_day=first_day) for seed in range(n)]


def shares(rolled):
    flat = [tie(d) for journey in rolled for d in journey]
    return {k: flat.count(k) / len(flat) for k in ("quiet", "story", "unrelated")}


@pytest.mark.parametrize("pacing,min_story,max_unrelated", [
    ("adventure", 0.40, 0.25), ("mixed", 0.25, 0.25), ("downtime", 0.15, 0.25)])
def test_most_road_days_are_quiet_or_tied_to_the_story(pacing, min_story, max_unrelated):
    mix = shares(journeys(pacing))
    assert mix["story"] >= min_story, mix
    assert mix["unrelated"] <= max_unrelated, mix
    assert mix["quiet"] >= 0.25, mix


def test_adventure_pacing_brings_more_story_than_quieter_pacings():
    story = {pacing: shares(journeys(pacing))["story"] for pacing in ("adventure", "mixed", "downtime")}
    assert story["adventure"] > story["mixed"] * 1.2 > story["downtime"] * 1.2, story


def test_the_road_still_brings_encounters():
    rolled = journeys("adventure", days=3)
    kinds = {d["kind"] for journey in rolled for d in journey}
    assert kinds == {"quiet", "traveller", "obstacle", "discovery", "danger", "story"}
    with_hazard = sum(1 for journey in rolled if any(d["kind"] in HAZARDS for d in journey))
    assert with_hazard > len(rolled) * 0.15


@pytest.mark.parametrize("first_day", [None, 100])
def test_at_most_one_hazard_per_journey(first_day):
    for journey in journeys("adventure", days=7, n=500, first_day=first_day):
        assert sum(d["kind"] in HAZARDS for d in journey) <= 1, [d["kind"] for d in journey]


def test_a_journey_carries_on_across_consecutive_days():
    recent = [{"kind": "danger", "text": "Wolves at dusk.", "day": 10}]
    ctx = context()
    for seed in range(300):
        rolled = oracle.roll_road(2, rng=random.Random(seed), context=ctx, recent=recent, first_day=11)
        assert not any(d["kind"] in HAZARDS for d in rolled), seed


def test_a_gap_in_the_days_starts_a_new_journey():
    recent = [{"kind": "danger", "text": "Wolves at dusk.", "day": 10}]
    ctx = context()
    hazards = sum(any(d["kind"] in HAZARDS for d in oracle.roll_road(
        2, rng=random.Random(seed), context=ctx, recent=recent, first_day=14)) for seed in range(300))
    assert hazards > 30


def test_racing_a_clock_allows_only_a_hazard_that_raises_its_stakes():
    obstacles = 0
    for journey in journeys("adventure", days=3, n=600, due=12, first_day=10):
        for day in journey:
            assert day["kind"] not in ("danger", "discovery"), day
            if day["kind"] == "obstacle":
                obstacles += 1
                assert day.get("stakes") and "abbey feast begins" in day["text"], day
            if day["kind"] == "traveller":
                assert day.get("source"), day
    assert obstacles > 0


@pytest.mark.parametrize("due", [40, 5])
def test_a_far_off_or_past_clock_is_not_a_race(due):
    kinds = {d["kind"] for journey in journeys("adventure", days=3, n=600, due=due, first_day=10) for d in journey}
    assert "danger" in kinds


def test_story_days_draw_on_the_beat_hooks_clocks_and_errand():
    texts = {d["text"] for journey in journeys("adventure", days=3, n=600) for d in journey if d["kind"] == "story"}
    for words in ("lean on every carter", "three casks", "ferryman", "abbey feast begins"):
        assert any(words in t for t in texts), words


def test_racing_story_days_favour_the_clock():
    rolled = journeys("adventure", days=1, n=3000, due=12, first_day=11)
    story = [d["text"] for journey in rolled for d in journey if d["kind"] == "story"]
    assert sum("abbey feast begins" in t for t in story) > len(story) * 0.3


def test_guidance_keeps_quiet_days_short_and_leaves_the_choice_to_the_party():
    assert "a line or two" in oracle._ROAD_ROWS["quiet"][0]
    for kind in ("traveller", "obstacle", "discovery", "danger", "story"):
        assert "party" in oracle._ROAD_ROWS[kind][0], kind
    assert "never" in oracle.ROAD_RULE.lower() and "for them" in oracle.ROAD_RULE


def test_blocking_the_road_is_an_obstacle_not_a_traveller():
    assert not any("block" in t for t in oracle._ROAD_ROWS["traveller"][1])


# ── through the campaign files and calendar.py ───────────────────────────────


def make(root, start="9 Rainmonth 900"):
    folder = root / "campaigns" / "Roadstory"
    folder.mkdir(parents=True)
    (folder / "state.md").write_text(state(), encoding="utf-8")
    calendar("init", "--date", start, "--months", ",".join(MONTHS), root=root)
    return folder


def calendar(*args, root):
    env = {**os.environ, "DND_CAMPAIGN_ROOT": str(root)}
    result = subprocess.run([sys.executable, str(SCRIPTS / "calendar.py"), "-c", "Roadstory", *args],
                            capture_output=True, text=True, encoding="utf-8", env=env)
    assert result.returncode == 0, result.stderr
    return result.stdout


def log(folder):
    return json.loads((folder / "road_log.json").read_text(encoding="utf-8"))


def test_campaign_road_dates_each_day_from_the_calendar(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    folder = make(tmp_path)
    for seed in range(40):
        for day in oracle.campaign_road(folder, 1, rng=random.Random(seed)):
            assert day["kind"] not in ("danger", "discovery"), day
    days = [entry["day"] for entry in log(folder)]
    assert len(set(days)) == 1 and days[0] == oracle.day_number(json.loads(
        (folder / "calendar.json").read_text(encoding="utf-8"))) + 1


def test_calendar_days_on_the_road_form_one_journey(tmp_path):
    folder = make(tmp_path, start="1 Frostmonth 900")
    for _ in range(9):
        out = calendar("advance", "1", "days", root=tmp_path)
        assert oracle.ROAD_RULE in out
    entries = log(folder)
    assert [e["day"] for e in entries] == list(range(entries[0]["day"], entries[0]["day"] + 9))
    assert sum(e["kind"] in HAZARDS for e in entries) <= 1


def test_oracle_road_cli_prints_the_rule(tmp_path):
    make(tmp_path)
    env = {**os.environ, "DND_CAMPAIGN_ROOT": str(tmp_path)}
    result = subprocess.run([sys.executable, str(SCRIPTS / "oracle.py"), "road", "--campaign", "Roadstory", "--seed", "3"],
                            capture_output=True, text=True, encoding="utf-8", env=env)
    assert result.returncode == 0, result.stderr
    assert oracle.ROAD_RULE in result.stdout and "Day 1 of 1" in result.stdout
