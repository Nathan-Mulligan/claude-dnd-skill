"""The sidebar party belongs to its campaign and survives campaign switches."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


DISPLAY = Path(__file__).resolve().parents[1] / "skills/dnd/display"
ELBANE = {"name": "Corin Ashby", "class": "Rogue", "level": 1, "hp": {"current": 10, "max": 10}}
KICK = {"name": "Pip Rowan", "class": "Monk", "level": 2, "hp": {"current": 14, "max": 14}}


@pytest.fixture
def display(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path / ".runtime"))
    monkeypatch.syspath_prepend(str(DISPLAY))
    import runtime_paths
    (tmp_path / ".runtime").mkdir(exist_ok=True)
    for name in ("Fenmoor", "Clockwork"):
        (tmp_path / "campaigns" / name).mkdir(parents=True)
    monkeypatch.setattr(runtime_paths, "RT", str(tmp_path / ".runtime"))
    spec = importlib.util.spec_from_file_location("_campaign_party_stats_test", DISPLAY / "dnd-display-app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.app.testing = True
    module._broadcast = lambda payload: None
    return module, module.app.test_client(), tmp_path


def select(client, name):
    """The portal's prepare_display: clear, then select the campaign."""
    assert client.post("/clear", json={}).status_code == 204
    assert client.post("/stats", json={"campaign": name}).status_code == 204


def names(module):
    return [p["name"] for p in module._current_stats.get("players", [])]


def test_switching_back_restores_the_campaign_party_without_the_dm(display):
    module, client, _ = display
    select(client, "Fenmoor")
    client.post("/stats", json={"players": [ELBANE], "replace_players": True})
    select(client, "Clockwork")
    client.post("/stats", json={"players": [KICK], "replace_players": True})
    select(client, "Fenmoor")
    assert names(module) == ["Corin Ashby"]
    select(client, "Clockwork")
    assert names(module) == ["Pip Rowan"]


def test_party_survives_a_display_restart(display):
    module, client, tmp_path = display
    select(client, "Fenmoor")
    client.post("/stats", json={"players": [ELBANE], "replace_players": True})
    saved = json.loads((tmp_path / "campaigns/Fenmoor/party_stats.json").read_text(encoding="utf-8"))
    assert [p["name"] for p in saved["players"]] == ["Corin Ashby"]
    with module._stats_lock:
        module._current_stats.clear()
    module._load_stats()
    assert names(module) == ["Corin Ashby"]


def test_clear_does_not_erase_the_saved_party(display):
    module, client, tmp_path = display
    select(client, "Fenmoor")
    client.post("/stats", json={"players": [ELBANE], "replace_players": True})
    assert client.post("/clear", json={}).status_code == 204
    client.post("/stats", json={"world_time": {"date": "16 Leaffall"}})
    assert (tmp_path / "campaigns/Fenmoor/party_stats.json").exists()
    saved = json.loads((tmp_path / "campaigns/Fenmoor/party_stats.json").read_text(encoding="utf-8"))
    assert [p["name"] for p in saved["players"]] == ["Corin Ashby"]


def test_new_campaign_starts_with_an_empty_sidebar(display):
    module, client, tmp_path = display
    select(client, "Fenmoor")
    client.post("/stats", json={"players": [ELBANE], "replace_players": True})
    (tmp_path / "campaigns/Fresh").mkdir()
    select(client, "Fresh")
    assert names(module) == []


def test_dm_push_still_updates_a_restored_party(display):
    module, client, _ = display
    select(client, "Fenmoor")
    client.post("/stats", json={"players": [ELBANE], "replace_players": True})
    select(client, "Clockwork")
    select(client, "Fenmoor")
    client.post("/stats", json={"players": [{"name": "Corin Ashby", "hp": {"current": 4, "max": 10}}]})
    player = module._current_stats["players"][0]
    assert player["hp"]["current"] == 4 and player["class"] == "Rogue"


def saved_names(tmp_path, campaign):
    path = tmp_path / "campaigns" / campaign / "party_stats.json"
    if not path.exists():
        return None
    return [p["name"] for p in json.loads(path.read_text(encoding="utf-8"))["players"]]


def test_new_campaign_party_never_lands_in_the_previous_campaign(display):
    module, client, tmp_path = display
    select(client, "Fenmoor")
    client.post("/stats", json={"players": [ELBANE], "replace_players": True})
    assert client.post("/clear", json={}).status_code == 204
    client.post("/stats", json={"players": [KICK], "replace_players": True})
    assert saved_names(tmp_path, "Fenmoor") == ["Corin Ashby"]
    client.post("/stats", json={"campaign": "Clockwork"})
    assert names(module) == ["Pip Rowan"]
    assert saved_names(tmp_path, "Clockwork") == ["Pip Rowan"]
    assert saved_names(tmp_path, "Fenmoor") == ["Corin Ashby"]


def test_partial_update_after_clear_keeps_the_full_saved_sheet(display):
    module, client, tmp_path = display
    select(client, "Fenmoor")
    client.post("/stats", json={"players": [ELBANE], "replace_players": True})
    assert client.post("/clear", json={}).status_code == 204
    client.post("/stats", json={"players": [{"name": "Corin Ashby", "hp": {"current": 4, "max": 10}}]})
    player = module._current_stats["players"][0]
    assert player["class"] == "Rogue" and player["hp"]["current"] == 4
    saved = json.loads((tmp_path / "campaigns/Fenmoor/party_stats.json").read_text(encoding="utf-8"))
    assert saved["players"][0]["class"] == "Rogue"


def test_startup_keeps_newer_runtime_details_with_the_saved_party(display):
    module, client, tmp_path = display
    select(client, "Fenmoor")
    client.post("/stats", json={"players": [ELBANE], "replace_players": True, "world_time": {"date": "old"}})
    Path(module.STATS_FILE).write_text(json.dumps({"world_time": {"date": "new"}}), encoding="utf-8")
    with module._stats_lock:
        module._current_stats.clear()
    module._load_stats()
    assert names(module) == ["Corin Ashby"]
    assert module._current_stats["world_time"] == {"date": "new"}
    assert module._stats_owner == "Fenmoor"


def test_startup_ignores_an_unowned_runtime_party(display):
    module, client, tmp_path = display
    select(client, "Fenmoor")
    client.post("/stats", json={"players": [ELBANE], "replace_players": True})
    Path(module.CAMP_FILE).write_text("Clockwork", encoding="utf-8")
    with module._stats_lock:
        module._current_stats.clear()
    module._load_stats()
    assert names(module) == []
    client.post("/stats", json={"campaign": "Clockwork"})
    assert saved_names(tmp_path, "Clockwork") in (None, [])
    assert saved_names(tmp_path, "Fenmoor") == ["Corin Ashby"]


def test_emptying_the_party_is_saved(display):
    module, client, tmp_path = display
    select(client, "Fenmoor")
    client.post("/stats", json={"players": [ELBANE], "replace_players": True})
    client.post("/stats", json={"players": [], "replace_players": True})
    select(client, "Clockwork")
    select(client, "Fenmoor")
    assert names(module) == []
