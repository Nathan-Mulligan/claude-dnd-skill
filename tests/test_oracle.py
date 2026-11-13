"""
test_oracle.py — chaos factor, yes/no, event focus, scene meaning oracles.

Run from repo root:
    python3 -m unittest tests.test_oracle -v
"""
import json
import pathlib
import random
import sys
import unittest

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "scripts"))

import oracle  # noqa: E402


class ChaosTests(unittest.TestCase):
    def test_clamp(self):
        self.assertEqual(oracle.clamp_chaos(0), 1)
        self.assertEqual(oracle.clamp_chaos(99), 9)
        self.assertEqual(oracle.clamp_chaos(5), 5)

    def test_adjust_direction(self):
        self.assertEqual(oracle.adjust_chaos(5, pc_proactive=True), 4)
        self.assertEqual(oracle.adjust_chaos(5, pc_proactive=False), 6)
        self.assertEqual(oracle.adjust_chaos(1, pc_proactive=True), 1)
        self.assertEqual(oracle.adjust_chaos(9, pc_proactive=False), 9)

    def test_read_default(self):
        self.assertEqual(oracle.read_chaos("## Session Flags\n- autorun: off\n"), 5)

    def test_read_explicit(self):
        self.assertEqual(oracle.read_chaos("## Session Flags\n- chaos_factor: 7\n"), 7)

    def test_write_inserts_under_section(self):
        text = "# Campaign\n\n## Session Flags\n- autorun: off\n"
        out = oracle.write_chaos(text, 8)
        self.assertIn("chaos_factor: 8", out)
        self.assertEqual(oracle.read_chaos(out), 8)

    def test_write_in_place_update(self):
        text = "## Session Flags\n- chaos_factor: 3\n"
        out = oracle.write_chaos(text, 6)
        self.assertEqual(oracle.read_chaos(out), 6)
        self.assertEqual(out.count("chaos_factor"), 1)

    def test_write_appends_section_when_missing(self):
        out = oracle.write_chaos("# Campaign\n", 4)
        self.assertIn("## Session Flags", out)
        self.assertEqual(oracle.read_chaos(out), 4)


class YesNoTests(unittest.TestCase):
    def test_seeded_reproducible(self):
        a = oracle.yes_no("50/50", 5, rng=random.Random(42))
        b = oracle.yes_no("50/50", 5, rng=random.Random(42))
        self.assertEqual(a, b)

    def test_verdict_shape(self):
        verdict, roll = oracle.yes_no("likely", 5, rng=random.Random(1))
        self.assertTrue(1 <= roll <= 100)
        base = verdict.split("-")[0]
        self.assertIn(base, {"yes", "no"})

    def test_likely_skews_yes(self):
        rng = random.Random(7)
        yes = sum(1 for _ in range(500)
                  if oracle.yes_no("sure-thing", 5, rng=rng)[0].startswith("yes"))
        self.assertGreater(yes, 400)

    def test_doubles_give_and(self):
        # Find a seed whose roll is a double; verdict must carry -and.
        for s in range(1000):
            verdict, roll = oracle.yes_no("50/50", 5, rng=random.Random(s))
            rs = str(roll).zfill(2)
            if rs[0] == rs[1]:
                self.assertTrue(verdict.endswith("-and"), f"{roll} -> {verdict}")
                return
        self.fail("no double roll found in 1000 seeds")


class EventFocusTests(unittest.TestCase):
    def test_in_table(self):
        labels = {lbl for _, lbl in oracle._FOCUS_TABLE}
        roll, label = oracle.random_event_focus(rng=random.Random(3))
        self.assertIn(label, labels)
        self.assertTrue(1 <= roll <= 100)

    def test_low_roll_is_remote(self):
        # Seed whose first randint(1,100) is <=5 maps to "remote event".
        for s in range(2000):
            roll, label = oracle.random_event_focus(rng=random.Random(s))
            if roll <= 5:
                self.assertEqual(label, "remote event")
                return
        self.fail("no roll <=5 found")


class SceneMeaningTests(unittest.TestCase):
    def test_pair_from_lists(self):
        action, subject = oracle.scene_meaning(rng=random.Random(9))
        self.assertIn(action, oracle._ACTIONS)
        self.assertIn(subject, oracle._SUBJECTS)

    def test_seeded_reproducible(self):
        a = oracle.scene_meaning(rng=random.Random(11))
        b = oracle.scene_meaning(rng=random.Random(11))
        self.assertEqual(a, b)


class ImprovVarietyTests(unittest.TestCase):
    def test_most_npcs_are_ordinary_and_a_few_stand_out(self):
        rng = random.Random(3)
        tiers = [oracle.npc_temperament(rng=rng)["tier"] for _ in range(4000)]
        self.assertTrue(2200 < tiers.count("ordinary") < 2600)
        self.assertTrue(1000 < tiers.count("colourful") < 1400)
        rare = tiers.count("memorable") + tiers.count("troubled")
        self.assertTrue(250 < rare < 550)
        self.assertGreater(tiers.count("troubled"), 50)
        self.assertLess(tiers.count("troubled"), tiers.count("memorable"))

    def test_about_half_of_wants_ask_something_of_the_party(self):
        rng = random.Random(4)
        asks = sum(oracle.npc_temperament(rng=rng)["asks"] for _ in range(2000))
        self.assertTrue(800 < asks < 1200)

    def test_traders_also_get_a_trading_style(self):
        npc = oracle.npc_temperament(rng=random.Random(1), trader=True)
        self.assertIn(npc["trades"], oracle._TRADE_STANCES)
        self.assertNotIn("trades", oracle.npc_temperament(rng=random.Random(1)))

    def test_most_road_days_are_quiet_but_not_all(self):
        rng = random.Random(5)
        kinds = [oracle.road_day(rng=rng)["kind"] for _ in range(2000)]
        self.assertTrue(600 < kinds.count("quiet") < 1000)
        self.assertEqual(set(kinds), {"quiet", "traveller", "obstacle", "discovery", "danger", "story"})

    def test_adventure_pacing_brings_fewer_quiet_days_and_more_story(self):
        def count(pacing):
            rng = random.Random(6)
            kinds = [oracle.road_day(rng=rng, pacing=pacing)["kind"] for _ in range(3000)]
            return kinds.count("quiet"), kinds.count("story")
        quiet_mixed, story_mixed = count("mixed")
        quiet_adv, story_adv = count("adventure")
        quiet_down, story_down = count("downtime")
        self.assertLess(quiet_adv, quiet_mixed * 0.7)
        self.assertGreater(story_adv, story_mixed * 2)
        self.assertGreater(quiet_down, quiet_mixed)
        self.assertLess(story_down, story_mixed)

    def test_each_row_has_several_examples(self):
        for kind, (_, examples) in oracle._ROAD_ROWS.items():
            self.assertGreaterEqual(len(examples), 5, kind)

    def test_prebuilt_campaigns_are_recognised(self):
        self.assertTrue(oracle.is_prebuilt("## Campaign Arc\ntype: structured \u2014 published module."))
        self.assertTrue(oracle.is_prebuilt("```yaml\ntype: \"structured\"\n```"))
        self.assertFalse(oracle.is_prebuilt("```yaml\ntype: dynamic\n```"))

    def test_campaign_kind(self):
        self.assertEqual(oracle.campaign_kind("```yaml\ntype: dynamic\n# type: structured\n```"), "dynamic")
        self.assertEqual(oracle.campaign_kind("type: sandbox"), "sandbox")
        self.assertEqual(oracle.campaign_kind("# Campaign\n"), None)
        self.assertTrue(oracle.is_improvised("type: sandbox"))
        self.assertFalse(oracle.is_improvised("type: structured"))


class RoadCliTests(unittest.TestCase):
    def run_road(self, *args):
        import subprocess
        return subprocess.run([sys.executable, str(SKILL / "scripts" / "oracle.py"), "road", *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_days_must_be_positive(self):
        for days in ("0", "-3"):
            self.assertNotEqual(self.run_road("--days", days).returncode, 0)

    def test_unknown_campaign_is_an_error(self):
        result = self.run_road("--campaign", "no-such-campaign-for-oracle-tests", "--seed", "1")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("day 1", result.stdout.lower())


STATE = """# Campaign

## Next Hooks
- Saltmere: the cooper is short of hoops and will pay for a crate carried from Brackenford.
- The Wardens want the burned wagon at the crossroads explained.

## Clocks
- 18 Duskfall 1247: the Wardens impound unregistered wagons at Brackenford (seen by: carters turning back on the road)

## Campaign Arc
```yaml
type: dynamic
```

## Session Flags
- pacing: adventure
- difficulty: standard
"""


def make_campaign(root, name="Roadtest", state=STATE):
    folder = root / "campaigns" / name
    folder.mkdir(parents=True)
    (folder / "state.md").write_text(state, encoding="utf-8")
    return folder


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    return make_campaign(tmp_path)


def test_campaign_road_reads_flags_hooks_and_clocks():
    road = oracle.read_road_context(STATE)
    assert road["pacing"] == "adventure"
    assert road["difficulty"] == "standard"
    assert any("cooper" in hook for hook in road["hooks"])
    clock = road["clocks"][0]
    assert clock["date"] == "18 Duskfall 1247"
    assert "impound" in clock["what"]
    assert clock["seen_by"] == "carters turning back on the road"


def test_story_days_come_from_hooks_and_clocks():
    context = oracle.read_road_context(STATE)
    texts = set()
    rng = random.Random(1)
    for _ in range(400):
        day = oracle.road_day(rng=rng, pacing="adventure", context=context)
        if day["kind"] == "story":
            texts.add(day["text"])
    assert any("cooper" in t for t in texts)
    assert any("impound" in t for t in texts)


def test_travellers_sometimes_bring_clock_news():
    context = oracle.read_road_context(STATE)
    rng = random.Random(2)
    travellers = [d["text"] for d in (oracle.road_day(rng=rng, context=context) for _ in range(400))
                  if d["kind"] == "traveller"]
    assert any("carters turning back" in t for t in travellers)
    assert any("carters turning back" not in t and "cooper" not in t and "Wardens" not in t for t in travellers)


def test_road_log_prevents_immediate_repeats(campaign):
    days = []
    for seed in range(30):
        days += oracle.campaign_road(campaign, 2, rng=random.Random(seed))
    for before, after in zip(days, days[1:]):
        assert before["text"] != after["text"]
        if before["kind"] != "quiet":
            assert before["kind"] != after["kind"]
    log = json.loads((campaign / "road_log.json").read_text(encoding="utf-8"))
    assert len(log) == 10
    assert log[-1]["text"] == days[-1]["text"]


def test_road_log_avoids_the_last_ten_examples(campaign):
    days = []
    for seed in range(40):
        days += oracle.campaign_road(campaign, 1, rng=random.Random(seed))
    for i, day in enumerate(days):
        assert day["text"] not in [d["text"] for d in days[max(0, i - 10):i]]


def test_danger_day_prints_an_encounter_budget_for_the_party(campaign):
    (campaign / "party_stats.json").write_text(json.dumps({"players": [
        {"name": "Elbane", "level": 1}, {"name": "Nella", "level": 1}]}), encoding="utf-8")
    party = oracle.read_party(campaign)
    assert party == (2, 1)
    lines = oracle.encounter_budget(party, "standard")
    assert "100 XP" in lines
    assert "2 characters" in lines and "level 1" in lines
    assert "CR 1/4" in lines


def test_party_falls_back_to_character_files(campaign):
    chars = campaign / "characters"
    chars.mkdir()
    (chars / "aldric.md").write_text("- **Race:** Human | **Class:** Fighter | **Level:** 3 |", encoding="utf-8")
    (chars / "mira.md").write_text("- **Level:** 4", encoding="utf-8")
    assert oracle.read_party(campaign) == (2, 4)


def test_danger_day_without_a_party_still_says_how_to_size_it():
    assert "xp.py calc" in oracle.encounter_budget(None, "hard")


def test_formatted_danger_day_includes_budget(campaign):
    (campaign / "party_stats.json").write_text(json.dumps({"players": [{"name": "A", "level": 2}]}), encoding="utf-8")
    for seed in range(200):
        days = oracle.campaign_road(campaign, 1, rng=random.Random(seed))
        if days[0]["kind"] == "danger":
            text = oracle.format_road_day(days[0], 1, 1)
            assert "Encounter budget" in text and "XP" in text
            return
    pytest.fail("no danger day in 200 seeds")


def run_oracle(root, *args):
    import os
    import subprocess
    env = {**os.environ, "DND_CAMPAIGN_ROOT": str(root)}
    return subprocess.run([sys.executable, str(SKILL / "scripts" / "oracle.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env)


def test_seeded_npc_is_stable_and_written_to_npcs_md(tmp_path):
    folder = make_campaign(tmp_path)
    (folder / "npcs.md").write_text(
        "# NPCs\n\n| Name | Role |\n|---|---|\n| Bera Holt | trader |\n\n"
        "### Tovin Marsh\n- **Role:** ferryman\n- Tells dry jokes.\n", encoding="utf-8")
    first = run_oracle(tmp_path, "npc", "--campaign", "Roadtest", "--name", "Bera Holt", "--trader")
    assert first.returncode == 0, first.stderr
    second = run_oracle(tmp_path, "npc", "--campaign", "Roadtest", "--name", "bera  holt", "--trader")
    assert second.returncode == 0, second.stderr
    strip = lambda out: [l for l in out.splitlines() if l.startswith(("temperament", "wants", "trades"))]
    assert strip(first.stdout) == strip(second.stdout) and strip(first.stdout)
    text = (folder / "npcs.md").read_text(encoding="utf-8")
    assert text.count("### Bera Holt") == 1
    assert text.count("**Temperament:**") == 1
    assert "**Wants now:**" in text and "**Trades:**" in text
    assert "### Tovin Marsh\n- **Role:** ferryman\n- Tells dry jokes." in text


def test_seeded_npc_updates_an_existing_entry_in_place(tmp_path):
    folder = make_campaign(tmp_path)
    (folder / "npcs.md").write_text(
        "# NPCs\n\n### Mara Venn (archivist)\n- **Role:** archivist\n\n### Personality\n- dry\n\n"
        "### Tovin Marsh\n- **Role:** ferryman\n", encoding="utf-8")
    assert run_oracle(tmp_path, "npc", "--campaign", "Roadtest", "--name", "Mara Venn").returncode == 0
    text = (folder / "npcs.md").read_text(encoding="utf-8")
    mara, tovin = text.split("### Tovin Marsh")
    assert "- **Role:** archivist\n- **Temperament:**" in mara
    assert "Temperament" not in tovin
    assert text.count("Mara Venn") == 1


def test_npc_note_appends_a_line_to_the_entry(tmp_path):
    folder = make_campaign(tmp_path)
    (folder / "calendar.json").write_text(json.dumps(
        {"day": 17, "month": 8, "year": 1247, "months": ["a", "b", "c", "d", "e", "f", "g", "Duskfall"]}),
        encoding="utf-8")
    (folder / "npcs.md").write_text(
        "# NPCs\n\n### Bera Holt\n- **Role:** trader\n\n### Personality\n- sly\n\n### Tovin Marsh\n- **Role:** ferryman\n",
        encoding="utf-8")
    note = "Sold 4 blankets at 18. Thinks they drive a hard bargain."
    result = run_oracle(tmp_path, "npc", "note", "--campaign", "Roadtest", "--name", "Bera Holt", note)
    assert result.returncode == 0, result.stderr
    run_oracle(tmp_path, "npc", "note", "--campaign", "Roadtest", "--name", "Bera Holt", "Paid them to carry a crate.")
    text = (folder / "npcs.md").read_text(encoding="utf-8")
    bera, tovin = text.split("### Tovin Marsh")
    assert f"- Party note (17 Duskfall 1247): {note}\n- Party note (17 Duskfall 1247): Paid them to carry a crate." in bera
    assert "Party note" not in tovin


def test_npc_note_creates_the_entry_when_missing(tmp_path):
    folder = make_campaign(tmp_path)
    result = run_oracle(tmp_path, "npc", "note", "--campaign", "Roadtest", "--name", "Jessa Pike", "Owes them a favour.")
    assert result.returncode == 0, result.stderr
    text = (folder / "npcs.md").read_text(encoding="utf-8")
    assert "### Jessa Pike\n- Party note: Owes them a favour." in text


def test_npc_note_needs_campaign_and_name(tmp_path):
    make_campaign(tmp_path)
    assert run_oracle(tmp_path, "npc", "note", "--campaign", "Roadtest", "text").returncode != 0
    assert run_oracle(tmp_path, "npc", "note", "--campaign", "Nope", "--name", "X", "text").returncode != 0


def test_road_cli_uses_the_campaign_and_logs(tmp_path):
    folder = make_campaign(tmp_path)
    result = run_oracle(tmp_path, "road", "--campaign", "Roadtest", "--days", "3", "--seed", "4")
    assert result.returncode == 0, result.stderr
    assert "Day 3 of 3" in result.stdout
    assert len(json.loads((folder / "road_log.json").read_text(encoding="utf-8"))) == 3


if __name__ == "__main__":
    unittest.main()
