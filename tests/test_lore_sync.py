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
    for kept in ("Find a return cargo", "toll doubled", "Sold the flour"):
        assert kept in text
    assert "SECRET" not in text
    assert "Road Wardens keep" not in text  # World State can hold hidden faction activity
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
        self.campaign, self.fail_post, self.on_model, self.old_server = "Test", False, None, False
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
        if path == "/stats/lore" and self.old_server:
            raise lore_sync.urllib.error.HTTPError(path, 404, "Not Found", {}, None)
        self.posts.append((path, json.loads(data)))
        return {}

    def model(self, prompt):
        self.calls.append(prompt)
        if self.on_model:
            self.on_model()
        return {"factions": [{"name": "Road Wardens", "detail": "Keep the toll road."}], "quests": []}


def test_run_skips_model_when_nothing_changed(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    assert lore_sync.run(settle_s=0) == "updated"
    path, body = live.posts[-1]
    assert path == "/stats/lore"  # notes only, never whole lists
    assert body["factions"] == {"Road Wardens": "Keep the toll road."} and body["campaign"] == "Test"
    assert lore_sync.run(settle_s=0) == "unchanged"
    assert len(live.calls) == 1 and "SECRET" not in live.calls[0]


def test_unreadable_stats_never_publishes_empty_lists(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    live.on_model = lambda: live.write_stats('{"factions": [')  # display mid-write
    lore_sync.run(settle_s=0)
    assert all(path == "/stats/lore" for path, _ in live.posts)  # never a list-replacing post


def test_failed_post_is_retried_next_time_without_new_model_call(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    live.fail_post = True
    try:
        lore_sync.run(settle_s=0)
    except OSError:
        pass
    assert lore_sync.load_cache("Test")["published"] is False
    live.fail_post = False
    assert lore_sync.run(settle_s=0) == "updated"
    assert len(live.calls) == 1  # reused the saved notes
    assert lore_sync.load_cache("Test")["published"] is True
    assert lore_sync.run(settle_s=0) == "unchanged"


def test_notes_are_saved_before_publishing(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    seen = []
    real = live.request

    def request(path, data=None, timeout=5):
        if path == "/stats/lore":  # a DM push landing now must already refill the new notes
            seen.append(lore_sync.load_cache("Test").get("factions"))
        return real(path, data, timeout)
    monkeypatch.setattr(lore_sync, "_request", request)
    lore_sync.run(settle_s=0)
    assert seen == [{"Road Wardens": "Keep the toll road."}]


def test_every_earlier_helper_note_is_sent_as_previous(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    lore_sync.run(settle_s=0)
    live.write_stats({"factions": [{"name": "Road Wardens", "standing": "Hostile"}], "quests": []})
    lore_sync.run(settle_s=0)
    _, body = live.posts[-1]
    assert body["previous"]["factions"]["Road Wardens"] == ["Keep the toll road."]


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


def test_old_display_never_gets_whole_lists_reposted(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    live.old_server = True
    assert lore_sync.run(settle_s=0) == "display-too-old"
    assert live.posts == []


def test_pending_work_at_pass_limit_is_rescheduled(monkeypatch, tmp_path):
    live = Live(monkeypatch, tmp_path)
    kicks = []
    monkeypatch.setattr(lore_sync, "kick", lambda: kicks.append(1))

    def push_every_pass():
        n = len(live.calls)
        live.write_stats({"factions": [{"name": "Road Wardens", "standing": "Hostile" if n % 2 else "Neutral"}], "quests": []})
        (tmp_path / "lore_sync.pending").touch()
    live.on_model = push_every_pass
    lore_sync.run(settle_s=0, max_passes=2)
    assert kicks == [1]


# ── display endpoint ─────────────────────────────────────────────────────────

import pytest  # noqa: E402

DISPLAY = REPO / "skills/dnd/display"


@pytest.fixture
def display(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path / ".runtime"))
    monkeypatch.syspath_prepend(str(DISPLAY))
    spec = importlib.util.spec_from_file_location("_lore_display_test", DISPLAY / "dnd-display-app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.app.testing = True
    monkeypatch.setattr(module, "_broadcast", lambda payload: None)
    monkeypatch.setattr(module, "_token_ok", lambda: True)
    monkeypatch.setattr(module, "_active_campaign_name", lambda: "Test")
    module._stats_owner = "Test"
    module._current_stats["factions"] = [
        {"name": "Guild", "standing": "Hostile"},                       # DM changed it meanwhile
        {"name": "Wardens", "standing": "Neutral", "detail": "old helper note"},
        {"name": "Court", "standing": "Allied", "detail": "written by the DM"},
    ]
    module._current_stats["quests"] = [{"name": "New quest", "status": "active"}]
    return module, module.app.test_client()


def test_lore_endpoint_sets_notes_without_replacing_lists(display):
    module, client = display
    r = client.post("/stats/lore", json={
        "campaign": "Test",
        "factions": {"Guild": "Guild note", "Court": "helper tries to overwrite"},
        "quests": {},
        "previous": {"factions": {"Wardens": "old helper note"}},
    })
    assert r.status_code == 204
    f = {x["name"]: x for x in module._current_stats["factions"]}
    assert f["Guild"] == {"name": "Guild", "standing": "Hostile", "detail": "Guild note"}
    assert "detail" not in f["Wardens"]            # stale helper note removed
    assert f["Court"]["detail"] == "written by the DM"  # DM note untouched
    assert module._current_stats["quests"] == [{"name": "New quest", "status": "active"}]


def test_lore_endpoint_refuses_other_campaign(display):
    module, client = display
    r = client.post("/stats/lore", json={"campaign": "Other", "factions": {"Guild": "x"}})
    assert r.status_code == 409
    assert "detail" not in module._current_stats["factions"][0]


def test_lore_endpoint_refuses_when_stats_belong_to_another_campaign(display):
    module, client = display
    module._stats_owner = "Other"  # switch in progress: stats already swapped
    r = client.post("/stats/lore", json={"campaign": "Test", "factions": {"Guild": "x"}})
    assert r.status_code == 409
    assert "detail" not in module._current_stats["factions"][0]


def test_cache_keeps_each_campaign_separately(monkeypatch, tmp_path):
    monkeypatch.setattr(lore_sync, "rt", lambda name: str(tmp_path / name))
    lore_sync.save_cache({"campaign": "A", "fingerprint": "a", "factions": {"Guild": "A note"}, "quests": {}})
    lore_sync.save_cache({"campaign": "B", "fingerprint": "b", "factions": {"Guild": "B note"}, "quests": {}})
    assert lore_sync.load_cache("A")["factions"] == {"Guild": "A note"}
    assert lore_sync.load_cache("B")["factions"] == {"Guild": "B note"}
    assert lore_sync.load_cache(None) == {}


def test_old_single_campaign_cache_still_reads(monkeypatch, tmp_path):
    monkeypatch.setattr(lore_sync, "rt", lambda name: str(tmp_path / name))
    (tmp_path / "lore_notes.json").write_text(json.dumps(
        {"campaign": "Enderal", "fingerprint": "f", "factions": {"Road Wardens": "n"}, "quests": {}}), encoding="utf-8")
    assert lore_sync.load_cache("Enderal")["factions"] == {"Road Wardens": "n"}


def test_lore_endpoint_accepts_a_list_of_earlier_helper_notes(display):
    module, client = display
    r = client.post("/stats/lore", json={"campaign": "Test", "factions": {"Wardens": "new"},
                                         "previous": {"factions": {"Wardens": ["older", "old helper note"]}}})
    assert r.status_code == 204
    f = {x["name"]: x for x in module._current_stats["factions"]}
    assert f["Wardens"]["detail"] == "new"
