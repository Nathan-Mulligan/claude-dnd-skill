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
    lore_sync.apply_cached(payload, cache)
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


def test_run_skips_model_when_nothing_changed(monkeypatch, tmp_path):
    camp = tmp_path / "campaigns" / "Test"
    camp.mkdir(parents=True)
    (camp / "state.md").write_text(STATE, encoding="utf-8")
    stats = {"factions": [{"name": "Road Wardens", "standing": "Neutral"}], "quests": []}
    monkeypatch.setattr(lore_sync, "rt", lambda name: str(tmp_path / name))
    (tmp_path / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    posts = []

    def fake_request(path, data=None):
        if path == "/health":
            return {"campaign": "Test", "data_root": str(tmp_path)}
        posts.append(json.loads(data))
        return {}

    calls = []

    def fake_model(prompt):
        calls.append(prompt)
        return {"factions": [{"name": "Road Wardens", "detail": "Keep the toll road."}], "quests": []}

    monkeypatch.setattr(lore_sync, "_request", fake_request)
    monkeypatch.setattr(lore_sync, "ask_model", fake_model)

    assert lore_sync.run(settle_s=0) == "updated"
    assert posts[-1]["factions"][0]["detail"] == "Keep the toll road."
    assert lore_sync.run(settle_s=0) == "unchanged"
    assert len(calls) == 1 and "SECRET" not in calls[0]
