"""Background faction/quest notes: secrets stay out, notes survive DM pushes."""
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills/dnd/display"))
spec = importlib.util.spec_from_file_location("lore_sync", REPO / "skills/dnd/display/lore_sync.py")
lore_sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lore_sync)

STATE = """# Campaign: Test
## Current Situation
- Party in town.
## World State
- **Faction states:** Road Wardens keep the toll road.
## Active Quests
- Find a return cargo.
## Open Threads & Rumours
- The toll doubled; nobody knows why.
## Next Hooks
- SECRET-HOOK: a courier carries the auditor's notice.
## Faction Moves
- SECRET-MOVE: the Wardens sent an auditor.
## Clocks
- SECRET-CLOCK: bridge closes on the 21st.
## Recent Events
- Sold the flour.
## Campaign Arc
SECRET-ARC
"""


def test_public_text_keeps_party_sections_and_drops_dm_secrets():
    text = lore_sync.public_text(STATE)
    for kept in ("Road Wardens keep the toll road", "Find a return cargo", "toll doubled", "Sold the flour"):
        assert kept in text
    assert "SECRET" not in text
    assert "Party in town" not in text


def test_prompt_never_contains_dm_sections():
    prompt = lore_sync.build_prompt(lore_sync.public_text(STATE),
                                    [{"name": "Road Wardens", "standing": "Neutral"}],
                                    [{"name": "Find cargo", "status": "active"}])
    assert "SECRET" not in prompt
    assert "Road Wardens (Neutral)" in prompt and "Find cargo (active)" in prompt


def test_fingerprint_ignores_details_but_tracks_status_and_notes():
    f = [{"name": "A", "standing": "Neutral"}]
    base = lore_sync.fingerprint("x", f, [])
    assert lore_sync.fingerprint("x", [{"name": "A", "standing": "Neutral", "detail": "d"}], []) == base
    assert lore_sync.fingerprint("x", [{"name": "A", "standing": "Hostile"}], []) != base
    assert lore_sync.fingerprint("y", f, []) != base


def test_apply_cached_fills_missing_detail_but_dm_detail_wins():
    cache = {"campaign": "Test", "factions": {"A": "saved"}, "quests": {"Q": "q-saved"}}
    payload = {"factions": [{"name": "A", "standing": "Neutral"}, {"name": "B", "standing": "Neutral"}],
               "quests": [{"name": "Q", "status": "active", "detail": "from DM"}]}
    lore_sync.apply_cached(payload, "Test", cache)
    assert payload["factions"][0]["detail"] == "saved"
    assert "detail" not in payload["factions"][1]
    assert payload["quests"][0]["detail"] == "from DM"


def test_clean_notes_drops_unknown_names_blanks_and_trims():
    result = [{"name": "A", "detail": "  ok  text "}, {"name": "Ghost", "detail": "invented"},
              {"name": "B", "detail": ""}, {"name": "C", "detail": "x" * 999}]
    notes = lore_sync.clean_notes(result, ["A", "B", "C"])
    assert notes["A"] == "ok text"
    assert "Ghost" not in notes and "B" not in notes
    assert len(notes["C"]) == lore_sync.DETAIL_MAX


def test_kick_is_off_when_disabled(monkeypatch):
    called = []
    monkeypatch.setenv("DND_LORE_SYNC", "0")
    monkeypatch.setattr(lore_sync.subprocess, "Popen", lambda *a, **k: called.append(a))
    lore_sync.kick()
    assert called == []


def test_apply_cached_needs_matching_known_campaign():
    cache = {"campaign": "A", "factions": {"Guild": "A's secret-ish note"}}
    for campaign in (None, "B"):
        payload = {"factions": [{"name": "Guild", "standing": "Neutral"}]}
        lore_sync.apply_cached(payload, campaign, cache)
        assert "detail" not in payload["factions"][0]


class Live:
    """Fake display + model around a temp runtime dir."""

    def __init__(self, monkeypatch, tmp_path, stats=None):
        camp = tmp_path / "campaigns" / "Test"
        camp.mkdir(parents=True)
        (camp / "state.md").write_text(STATE, encoding="utf-8")
        self.tmp, self.posts, self.calls = tmp_path, [], []
        self.campaign, self.fail_post, self.on_model = "Test", False, None
        monkeypatch.setattr(lore_sync, "rt", lambda name: str(tmp_path / name))
        self.write_stats(stats or {"factions": [{"name": "Road Wardens", "standing": "Neutral"}], "quests": []})
        monkeypatch.setattr(lore_sync, "_request", self.request)
        monkeypatch.setattr(lore_sync, "ask_model", self.model)
        monkeypatch.setattr(lore_sync.time, "sleep", lambda s: None)

    def write_stats(self, stats):
        (self.tmp / "stats.json").write_text(json.dumps(stats) if isinstance(stats, dict) else stats, encoding="utf-8")

    def request(self, path, data=None, timeout=5):
        if path == "/health":
            return {"campaign": self.campaign, "data_root": str(self.tmp)}
        if self.fail_post:
            raise OSError("display down")
        self.posts.append(json.loads(data))
        return {}

    def model(self, prompt):
        self.calls.append(prompt)
        if self.on_model:
            self.on_model()
        return {"factions": [{"name": "Road Wardens", "detail": "Keep the toll road."}], "quests": []}


def test_run_skips_model_when_nothing_changed(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    assert lore_sync.run(settle_s=0) == "updated"
    assert live.posts[-1]["factions"][0]["detail"] == "Keep the toll road."
    assert "campaign" not in live.posts[-1]  # would switch campaigns on the display
    assert lore_sync.run(settle_s=0) == "unchanged"
    assert len(live.calls) == 1 and "SECRET" not in live.calls[0]


def test_unreadable_stats_never_publishes_empty_lists(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    live.on_model = lambda: live.write_stats('{"factions": [')  # display mid-write
    assert lore_sync.run(settle_s=0) == "unreadable"
    assert live.posts == []


def test_failed_post_is_retried_next_time(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    live.fail_post = True
    try:
        lore_sync.run(settle_s=0)
    except OSError:
        pass
    assert not (tmp_path / "lore_notes.json").exists()
    live.fail_post = False
    assert lore_sync.run(settle_s=0) == "updated"
    assert len(live.calls) == 2


def test_campaign_switch_during_generation_blocks_publish(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    live.on_model = lambda: setattr(live, "campaign", "Other")
    assert lore_sync.run(settle_s=0) == "campaign-changed"
    assert live.posts == []


def test_push_while_busy_is_replayed(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)

    def change_during_model():
        if len(live.calls) == 1:
            live.write_stats({"factions": [{"name": "Road Wardens", "standing": "Hostile"}], "quests": []})
            assert lore_sync.run(settle_s=0) == "busy"  # second kick while the first holds the lock
    live.on_model = change_during_model
    assert lore_sync.run(settle_s=0) == "updated,updated"
    assert len(live.calls) == 2 and "Road Wardens (Hostile)" in live.calls[1]
