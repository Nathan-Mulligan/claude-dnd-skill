"""Model handoffs keep pending replies safe and stop concurrent submissions."""

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
    spec = importlib.util.spec_from_file_location("_campaign_handoff_display_test", DISPLAY / "dnd-display-app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.app.testing = True
    module._broadcast = lambda payload: None
    client = module.app.test_client()
    client.environ_base["HTTP_X_DND_DEVICE"] = "test-party-device"
    return module, client



def acquire(module, client, token="a" * 32):
    Path(module.CAMP_FILE).write_text("fresh", encoding="utf-8")
    return client.post("/campaign-handoff", json={"campaign":"fresh", "token":token, "active":True})


def test_handoff_blocks_new_inputs_and_releases_after_campaign_change(display):
    module, client = display
    assert acquire(module, client).status_code == 204
    body = {"character":"Everybody", "text":"Do not lose this reply"}
    for path in ("/player-input", "/player-input/stage", "/player-input/ready", "/player-input/skip"):
        assert client.post(path, json=body).status_code == 409
    assert client.post("/clear", json={}).status_code == 204
    Path(module.CAMP_FILE).write_text("next", encoding="utf-8")
    assert client.post("/campaign-handoff", json={"campaign":"fresh", "token":"b"*32, "active":False}).status_code == 409
    assert client.post("/campaign-handoff", json={"campaign":"fresh", "token":"a"*32, "active":False}).status_code == 204
    assert client.post("/player-input/stage", json=body).status_code == 204


def test_pending_reply_is_not_deleted_by_handoff(display):
    module, client = display
    body = {"character":"Everybody", "text":"A pending reply"}
    assert client.post("/player-input/stage", json=body).status_code == 204
    before = Path(module.QUEUE_FILE).read_bytes()
    assert acquire(module, client).status_code == 409
    assert Path(module.QUEUE_FILE).read_bytes() == before


def test_handoff_expires_if_portal_disappears(display, monkeypatch):
    module, client = display
    assert acquire(module, client).status_code == 204
    expiry = module._input_handoff["until"]
    monkeypatch.setattr(module._time, "monotonic", lambda: expiry + 1)
    assert client.post("/player-input/stage", json={"character":"Everybody", "text":"Continue"}).status_code == 204


def test_story_narration_clears_old_character_setup_question(display):
    module, client = display
    client.post("/setup-question", json={"text":"Which abilities?"})
    client.post("/chunk", json={"text":"Your new adventure begins."})
    assert module._setup_question == ""


def test_handoff_requires_authentication(display):
    module, client = display
    module._lan_token = "required-token"
    assert acquire(module, client).status_code == 403
