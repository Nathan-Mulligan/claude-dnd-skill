"""Browser portal guards and model dispatch, without starting a real CLI."""

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("campaign_portal", REPO / "scripts/campaign_portal.py")
portal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portal)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(portal, "RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(portal, "META_FILE", tmp_path / ".runtime" / "portal-session.json")
    app = portal.create_app()
    app.testing = True
    test_client = app.test_client()
    page = test_client.get("/", base_url="http://127.0.0.1:5002")
    assert page.status_code == 200
    token = re.search(rb'const token = "([^"]+)";', page.data).group(1).decode()
    return test_client, token


def api(client, path, *, method="get", data=None, origin="http://127.0.0.1:5002", token=True):
    test_client, secret = client
    headers = {"Origin": origin}
    if token:
        headers["X-DND-Portal-Token"] = secret
    return getattr(test_client, method)(path, base_url="http://127.0.0.1:5002", headers=headers, json=data)


def test_missing_token_and_wrong_origin_rejected(client):
    assert api(client, "/api/state", token=False).status_code == 403
    body = {"provider": "qwen", "mode": "new", "name": "demo", "description": "start"}
    assert api(client, "/api/start", method="post", data=body, origin="http://evil.example").status_code == 403


def test_new_start_builds_interactive_qwen_session(client, monkeypatch):
    calls = []

    def fake_run(*args, timeout=15):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(portal, "run", fake_run)
    monkeypatch.setattr(portal, "running", lambda: False)
    response = api(client, "/api/start", method="post", data={
        "provider": "qwen", "mode": "new", "name": "moon-road", "description": "a haunted forest",
    })
    assert response.status_code == 200
    assert any("--start" in args and "qwen" in args for args in calls)
    tmux = next(args for args in calls if args[:2] == ("tmux", "new-session"))
    assert "-i" in tmux[-1]
    assert "a haunted forest" in tmux[-1]
    assert "moon-road" in tmux[-1]
    assert portal.META_FILE.is_file()


def test_start_refuses_second_model(client, monkeypatch):
    monkeypatch.setattr(portal, "running", lambda: True)
    response = api(client, "/api/start", method="post", data={
        "provider": "codex", "mode": "new", "name": "demo", "description": "start",
    })
    assert response.status_code == 409
    assert "already running" in response.json["message"]


def test_resume_requires_existing_save(client, monkeypatch):
    monkeypatch.setattr(portal, "running", lambda: False)
    response = api(client, "/api/start", method="post", data={
        "provider": "codex", "mode": "resume", "name": "missing", "description": "",
    })
    assert response.status_code == 409
    assert "not found" in response.json["message"]


def test_send_rejects_multiline_and_idle_session(client, monkeypatch):
    assert api(client, "/api/send", method="post", data={"text": "hello\nworld"}).status_code == 400
    monkeypatch.setattr(portal, "running", lambda: False)
    assert api(client, "/api/send", method="post", data={"text": "hello"}).status_code == 409


def test_send_passes_literal_reply_to_tmux(client, monkeypatch):
    calls = []
    monkeypatch.setattr(portal, "running", lambda: True)

    def fake_run(*args, timeout=15):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(portal, "run", fake_run)
    response = api(client, "/api/send", method="post", data={"text": "I choose the forest; no scripts"})
    assert response.status_code == 200
    assert calls[0][-2:] == ("--", "I choose the forest; no scripts")
    assert calls[1][-1] == "Enter"
