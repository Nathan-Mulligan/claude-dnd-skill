"""The turn card preps the numbers for a trade, a fight or a journey, so the DM reads them instead of looking them up.

Plain scripts, no model: prices from the item list, foes from the monster list, and the next road day,
rolled once and kept until calendar.py (or oracle.py road) uses it, so a journey is never rolled twice.
"""

import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skills/dnd/scripts"
FIXTURE = REPO / "tests/fixtures/fenmoor"
SHEETS = REPO / "tests/fixtures/enderal_sheets"
sys.path.insert(0, str(SCRIPTS))

import oracle  # noqa: E402
import turn_prep  # noqa: E402

spec = importlib.util.spec_from_file_location("turn_card", REPO / "scripts/turn_card.py")
turn_card = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = turn_card
spec.loader.exec_module(turn_card)


@pytest.fixture
def fenmoor(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    folder = tmp_path / "campaigns" / "Fenmoor"
    shutil.copytree(FIXTURE, folder)
    (tmp_path / ".runtime").mkdir()
    return folder


def prepared(card_text):
    return [part for part in card_text.split(" | ") if part.startswith("Prepared")]


def card(folder, said):
    return turn_card.build(folder, f"[Corin Ashby]: {said}")


def calendar(folder, *args):
    env = {**os.environ, "DND_CAMPAIGN_ROOT": str(folder.parent.parent)}
    result = subprocess.run([sys.executable, str(SCRIPTS / "calendar.py"), "-c", folder.name, *args],
                            capture_output=True, text=True, encoding="utf-8", env=env)
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("said, kind", [
    ("I draw my blade and attack the bandits", "fight"),
    ("We'd like to buy a rope and a hooded lantern", "trade"),
    ("How much for the blankets?", "trade"),
    ("We set out for Ashbridge at first light", "journey"),
    ("We head north along the river road", "journey"),
    ("We ask Rosa how her daughter is keeping", None),
    ("[[Pass the salt, please]]", None),
    ("We will not set out until the messenger returns", None),
    ("We head out of the shop and cross the street to the inn", None),
    ("We refuse to attack the bandits", None),
    ("If they attack us we run. We set out at dawn", "journey"),
])
def test_only_a_clear_signal_starts_a_prep(said, kind):
    assert turn_prep.signal(said) == kind


def test_a_fight_preps_the_named_foes_stat_block(fenmoor):
    lines = prepared(card(fenmoor, "I draw my blade and attack the bandits"))
    assert len(lines) == 1
    line = lines[0]
    assert "Bandit: AC 12, HP 11 (2d8 + 2)" in line
    assert "Scimitar (1d6 slashing)" in line and "Light Crossbow (1d8 piercing)" in line
    assert "CR 1/8" in line


def test_a_fight_with_no_known_creature_preps_nothing(fenmoor):
    assert prepared(card(fenmoor, "I attack Garrick before he can speak")) == []


def test_a_trade_preps_list_prices_and_the_purses(fenmoor):
    (fenmoor / "characters").mkdir()
    shutil.copy(SHEETS / "nella-reed.md", fenmoor / "characters/nella-reed.md")
    ledger = {"version": 1, "coins": {"pp": 0, "gp": 32, "ep": 0, "sp": 8, "cp": 7}, "items": []}
    (fenmoor / "characters/nella-reed.inventory.json").write_text(json.dumps(ledger), encoding="utf-8")
    lines = prepared(card(fenmoor, "We'd like to buy a rope and a hooded lantern"))
    assert len(lines) == 1
    assert "Rope 1 gp" in lines[0] and "Lantern, Hooded 5 gp" in lines[0]
    assert "Nella Reed 32 gp, 8 sp, 7 cp" in lines[0]


def test_a_journey_preps_the_next_road_day_without_moving_time(fenmoor):
    calendar_before = (fenmoor / "calendar.json").read_text(encoding="utf-8")
    lines = prepared(card(fenmoor, "We set out for Ashbridge at first light"))
    assert len(lines) == 1
    pending = json.loads((fenmoor / oracle.ROAD_NEXT).read_text(encoding="utf-8"))
    assert pending["day"]["text"] in lines[0]
    assert "calendar.py" in lines[0]
    assert (fenmoor / "calendar.json").read_text(encoding="utf-8") == calendar_before
    assert not (fenmoor / "road_log.json").exists()


def test_asking_again_shows_the_same_road_day(fenmoor):
    first = prepared(card(fenmoor, "We set out for Ashbridge"))
    for _ in range(5):
        assert prepared(card(fenmoor, "We set out for Ashbridge")) == first


def test_calendar_advance_uses_the_prepared_day_instead_of_rolling_again(fenmoor):
    card(fenmoor, "We set out for Ashbridge")
    pending = json.loads((fenmoor / oracle.ROAD_NEXT).read_text(encoding="utf-8"))["day"]
    out = calendar(fenmoor, "advance", "2", "days")
    day_one = next(line for line in out.splitlines() if line.startswith("Day 1 of 2"))
    assert pending["text"] in day_one and f": {pending['kind']}." in day_one
    assert "Day 2 of 2" in out
    assert not (fenmoor / oracle.ROAD_NEXT).exists()
    log = json.loads((fenmoor / "road_log.json").read_text(encoding="utf-8"))
    assert len(log) == 2 and log[0]["text"] == pending["text"]


def test_oracle_road_uses_the_prepared_day_too(fenmoor):
    card(fenmoor, "We set out for Ashbridge")
    pending = json.loads((fenmoor / oracle.ROAD_NEXT).read_text(encoding="utf-8"))["day"]
    assert oracle.campaign_road(fenmoor, 1)[0]["text"] == pending["text"]
    assert not (fenmoor / oracle.ROAD_NEXT).exists()


def test_a_prepared_day_from_an_earlier_date_is_dropped(fenmoor):
    card(fenmoor, "We set out for Ashbridge")
    calendar(fenmoor, "advance", "1", "days", "--no-road")
    assert (fenmoor / oracle.ROAD_NEXT).exists()
    fresh = oracle.campaign_road(fenmoor, 1, rng=random.Random(7))
    (fenmoor / "road_log.json").unlink()
    assert fresh == oracle.campaign_road(fenmoor, 1, rng=random.Random(7))
    assert not (fenmoor / oracle.ROAD_NEXT).exists()


def test_a_prebuilt_campaign_gets_no_road_prep(fenmoor):
    path = fenmoor / "state.md"
    path.write_text(path.read_text(encoding="utf-8").replace("type: dynamic", "type: structured"), encoding="utf-8")
    assert prepared(card(fenmoor, "We set out for Ashbridge")) == []
    assert not (fenmoor / oracle.ROAD_NEXT).exists()


@pytest.mark.parametrize("said", [
    "I draw my blade and attack the bandits",
    "We'd like to buy a rope and a hooded lantern",
    "We set out for Ashbridge at first light",
])
def test_the_prep_is_one_short_line(fenmoor, said):
    text = card(fenmoor, said)
    assert len(prepared(text)) == 1 and len(prepared(text)[0]) < 520
    assert "\n" not in text


def test_no_signal_leaves_the_card_as_it_was(fenmoor):
    assert prepared(card(fenmoor, "We ask Rosa how her daughter is keeping")) == []
    assert not (fenmoor / oracle.ROAD_NEXT).exists()


def keep(folder, day, when=None):
    when = when if when is not None else oracle._today(folder)
    (folder / oracle.ROAD_NEXT).write_text(json.dumps({"for": when, "day": day}), encoding="utf-8")


KEPT = {"kind": "quiet", "text": "A kept quiet day.", "guidance": "One line.", "prep": "abc"}


def test_a_failed_log_write_keeps_the_prepared_day(fenmoor, monkeypatch):
    keep(fenmoor, KEPT)
    real = oracle._write_atomic

    def broken(path, text):
        if path.name == oracle.ROAD_LOG:
            raise OSError("disk full")
        real(path, text)

    monkeypatch.setattr(oracle, "_write_atomic", broken)
    with pytest.raises(OSError):
        oracle.campaign_road(fenmoor, 1)
    monkeypatch.setattr(oracle, "_write_atomic", real)
    assert oracle.campaign_road(fenmoor, 1)[0]["text"] == KEPT["text"]


def test_a_prepared_day_already_in_the_log_is_not_used_again(fenmoor):
    keep(fenmoor, KEPT)
    (fenmoor / oracle.ROAD_LOG).write_text(json.dumps([{"kind": "quiet", "text": KEPT["text"], "prep": "abc"}]),
                                          encoding="utf-8")
    assert oracle.campaign_road(fenmoor, 1)[0].get("prep") != "abc"
    assert not (fenmoor / oracle.ROAD_NEXT).exists()


def test_a_day_built_on_a_hook_that_has_gone_is_rolled_again(fenmoor):
    stale = {**KEPT, "kind": "story", "source": "hook:Rescue the trapped courier", "prep": "old"}
    keep(fenmoor, stale)
    assert oracle.prepare_road(fenmoor).get("prep") != "old"
    keep(fenmoor, stale)
    assert oracle.campaign_road(fenmoor, 1)[0].get("prep") != "old"


def test_a_day_kept_for_a_later_date_survives_an_earlier_journey(fenmoor):
    later = [9999, 1, 1]
    keep(fenmoor, KEPT, later)
    oracle.campaign_road(fenmoor, 1)
    assert json.loads((fenmoor / oracle.ROAD_NEXT).read_text(encoding="utf-8"))["for"] == later


@pytest.mark.skipif(oracle.fcntl is None, reason="no file locks on this platform")
def test_preparing_waits_while_the_calendar_holds_the_road(fenmoor):
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.path.insert(0, sys.argv[1]); import oracle, pathlib\n"
         "with oracle.road_lock(pathlib.Path(sys.argv[2])):\n    print('held', flush=True); time.sleep(1.5)",
         str(SCRIPTS), str(fenmoor)], stdout=subprocess.PIPE, text=True, encoding="utf-8")
    assert holder.stdout.readline().strip() == "held"
    import time
    began = time.monotonic()
    oracle.prepare_road(fenmoor)
    assert time.monotonic() - began > 1.0
    holder.wait(timeout=10)
