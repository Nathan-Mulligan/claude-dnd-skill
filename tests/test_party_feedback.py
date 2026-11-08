"""Party replies and DM progress stay visible during the current campaign."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


DISPLAY = Path(__file__).resolve().parents[1] / "skills/dnd/display"


@pytest.fixture
def display(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path / ".runtime"))
    monkeypatch.syspath_prepend(str(DISPLAY))
    import runtime_paths
    (tmp_path / ".runtime").mkdir(exist_ok=True)
    monkeypatch.setattr(runtime_paths, "RT", str(tmp_path / ".runtime"))
    spec = importlib.util.spec_from_file_location("_party_feedback_test", DISPLAY / "dnd-display-app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.app.testing = True
    module._broadcast = lambda payload: None
    client = module.app.test_client()
    client.environ_base["HTTP_X_DND_DEVICE"] = "test-party-device"
    return module, client


def test_submitted_reply_survives_next_question_and_reconnect(display):
    module, client = display
    assert client.post("/player-input/stage", json={"character": "Everybody", "text": "Lots of magic"}).status_code == 204
    assert client.post("/queue/consumed", json={}).status_code == 204
    state = module._party_feedback_snapshot()
    assert state["status"] == "working"
    assert state["responses"][0]["text"] == "Lots of magic"
    assert client.post("/setup-question", json={"text": "Where do you begin?"}).status_code == 204
    assert module._party_feedback_snapshot()["status"] == "waiting"
    response = client.get("/stream", buffered=False)
    try:
        packets = [next(response.response).decode("utf-8") for _ in range(5)]
        assert any('"party_feedback"' in packet and "Lots of magic" in packet for packet in packets)
    finally:
        response.close()


def test_new_campaign_clears_feedback_and_pending_answers(display):
    module, client = display
    assert client.post("/player-input/stage", json={"character": "Everybody", "text": "Old answer"}).status_code == 204
    client.post("/queue/consumed", json={})
    module._staged["Everybody"] = {"text": "Old draft", "ready": False}
    assert client.post("/clear", json={}).status_code == 204
    state = module._party_feedback_snapshot()
    assert state["status"] == "idle"
    assert state["responses"] == []
    assert module._staged == {}
    assert module._queue_status == []
    assert not Path(module.QUEUE_FILE).exists()


def test_progress_distinguishes_response_from_new_question(display):
    module, client = display
    client.post("/queue/consumed", json={})
    client.post("/chunk", json={"text": "Let me describe your options."})
    assert module._party_feedback_snapshot()["status"] == "responding"
    client.post("/setup-question", json={"text": "Which path?"})
    assert module._party_feedback_snapshot()["status"] == "waiting"
    client.post("/dm-status", json={"status": "stopped"})
    assert module._party_feedback_snapshot()["status"] == "stopped"


def test_old_campaign_cannot_change_current_progress(display):
    module, client = display
    Path(module.CAMP_FILE).write_text("fresh", encoding="utf-8")
    assert client.post("/queue/consumed", json={"campaign": "old"}).status_code == 409
    assert client.post("/dm-status", json={"status": "stopped", "campaign": "old"}).status_code == 409
    assert module._party_feedback_snapshot()["status"] == "idle"


def test_restart_does_not_replay_another_campaign(display):
    module, _ = display
    Path(module.CAMP_FILE).write_text("fresh", encoding="utf-8")
    entries = [{"_camp": "old", "text": "Old volcano"}, {"_camp": "fresh", "text": "New adventure"}]
    Path(module.LOG_FILE).write_text(json.dumps(entries), encoding="utf-8")
    module._load_log()
    assert list(module._text_log) == [entries[1]]
    assert json.loads(Path(module.LOG_FILE).read_text(encoding="utf-8")) == entries
