"""Browser portal guards and model dispatch, without starting a real CLI."""

import importlib.util
import io
import json
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
    monkeypatch.setattr(portal, "prepare_display", lambda name, **kwargs: None)
    monkeypatch.setattr(portal, "notify_party_consumed", lambda *args: None)
    app = portal.create_app()
    app.testing = True
    test_client = app.test_client()
    page = test_client.get("/", base_url="http://127.0.0.1:5002")
    assert page.status_code == 200
    health = test_client.get("/health", base_url="http://127.0.0.1:5002")
    assert health.json["data_root"] == str(tmp_path)
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


def test_health_reports_recent_browser_poll(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(portal, "running", lambda: False)
    health = lambda: test_client.get("/health", base_url="http://127.0.0.1:5002").json
    assert health()["browser_recent"] is False
    assert api(client, "/api/state").status_code == 200
    assert health()["browser_recent"] is True


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
    monkeypatch.setattr(portal, "CODEX_ENABLED", True)
    response = api(client, "/api/start", method="post", data={
        "provider": "codex", "mode": "new", "name": "demo", "description": "start",
    })
    assert response.status_code == 409
    assert "already running" in response.json["message"]


def test_new_campaign_selects_clean_display_before_starting_model(client, monkeypatch):
    events = []
    monkeypatch.setattr(portal, "running", lambda: False)
    monkeypatch.setattr(portal, "prepare_display", lambda name, **kwargs: events.append(("display", name)))

    def fake_run(*args, timeout=15):
        events.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(portal, "run", fake_run)
    response = api(client, "/api/start", method="post", data={
        "provider": "qwen", "mode": "new", "name": "fresh", "description": "a new story",
    })
    assert response.status_code == 200
    display = events.index(("display", "fresh"))
    launch = next(i for i, event in enumerate(events) if event[:2] == ("tmux", "new-session"))
    assert display < launch
    assert "DND_ACTIVE_CAMPAIGN=fresh" in events[launch][-1]


def test_display_failure_prevents_model_start(client, monkeypatch):
    monkeypatch.setattr(portal, "running", lambda: False)
    calls = []

    def failed_display(name, **kwargs):
        raise OSError("display unavailable")

    monkeypatch.setattr(portal, "prepare_display", failed_display)
    monkeypatch.setattr(portal, "run", lambda *args, **kwargs:
                        (calls.append(args), subprocess.CompletedProcess(args, 0, "", ""))[1])
    response = api(client, "/api/start", method="post", data={
        "provider": "qwen", "mode": "new", "name": "fresh", "description": "a new story",
    })
    assert response.status_code == 409
    assert not any(args[:2] == ("tmux", "new-session") for args in calls)


def test_display_preparation_checks_root_before_mutating(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    calls = []

    def wrong_display(request, **kwargs):
        calls.append(request)
        return io.BytesIO(b'{"data_root":"/another/root","campaign":"old"}')

    monkeypatch.setattr(portal.urllib.request, "urlopen", wrong_display)
    with pytest.raises(ValueError, match="different campaign data root"):
        portal.prepare_display("fresh")
    assert calls == ["http://127.0.0.1:5001/health"]


def test_display_preparation_requires_confirmed_campaign_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(portal, "RUNTIME", tmp_path / ".runtime")
    requests = []

    def unchanged_display(request, **kwargs):
        if isinstance(request, str):
            return io.BytesIO(json.dumps({"data_root": str(tmp_path), "campaign": "old"}).encode("utf-8"))
        requests.append(request)
        return io.BytesIO(b"")

    monkeypatch.setattr(portal.urllib.request, "urlopen", unchanged_display)
    with pytest.raises(ValueError, match="did not select"):
        portal.prepare_display("fresh")
    assert [request.full_url for request in requests] == [
        "http://127.0.0.1:5001/clear", "http://127.0.0.1:5001/stats",
    ]


def test_model_exiting_before_first_poll_reports_stopped_once(monkeypatch):
    monkeypatch.setattr(portal, "running", lambda: False)
    monkeypatch.setattr(portal, "read_meta", lambda: {"campaign": "fresh", "started_at": 123})
    notifications = []
    monkeypatch.setattr(portal, "notify_display", lambda *args: notifications.append(args) or True)
    pauses = iter([None, None])
    monkeypatch.setattr(portal.time, "sleep", lambda _: next(pauses))
    with pytest.raises(StopIteration):
        portal.party_input_loop()
    assert notifications == [("/dm-status", {"status": "stopped"})]


def test_resume_requires_existing_save(client, monkeypatch):
    monkeypatch.setattr(portal, "running", lambda: False)
    response = api(client, "/api/start", method="post", data={
        "provider": "qwen", "mode": "resume", "name": "missing", "description": "",
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
    assert calls[0][3] == "dnd-campaign-portal:0.0"
    assert calls[1][-1] == "Enter"


def test_codex_can_write_selected_campaign_root(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    args = portal.model_args("codex", "new", "demo", "start")
    assert args[args.index("-m") + 1] == "gpt-6.1-terra"
    assert args[args.index("--add-dir") + 1] == str(tmp_path)
    assert args[args.index("-s") + 1] == "workspace-write"
    assert "sandbox_workspace_write.network_access=true" in args


def test_codex_can_be_disabled_explicitly(client, monkeypatch):
    monkeypatch.setattr(portal, "CODEX_ENABLED", False)
    response = api(client, "/api/start", method="post", data={
        "provider": "codex", "mode": "new", "name": "demo", "description": "start",
    })
    assert response.status_code == 409
    assert "disabled" in response.json["message"]


def test_party_input_is_sent_to_active_model(client, monkeypatch):
    root = portal.RUNTIME
    root.mkdir(parents=True, exist_ok=True)
    portal.META_FILE.write_text('{"campaign":"demo","mode":"new"}', encoding="utf-8")
    (root / ".campaign").write_text("demo", encoding="utf-8")
    queue = root / ".input_queue"
    queue.write_text("[Everybody]: My name is Enther", encoding="utf-8")
    calls = []
    monkeypatch.setattr(portal, "running", lambda: True)
    monkeypatch.setattr(portal, "run", lambda *args, **kwargs: (calls.append(args), subprocess.CompletedProcess(args, 0, "", ""))[1])
    monkeypatch.setattr(portal, "notify_party_consumed", lambda: calls.append(("consumed",)))

    assert portal.deliver_party_input() is True
    assert not queue.exists()
    assert any("My name is Enther" in " ".join(args) for args in calls)
    assert any("setup question" in " ".join(args) for args in calls)
    assert any(portal.SCENE_PROTOCOL in " ".join(args) for args in calls)
    assert calls[-1] == ("consumed",)
    assert portal.deliver_party_input() is False


def test_party_input_waits_for_matching_campaign(client, monkeypatch):
    root = portal.RUNTIME
    root.mkdir(parents=True, exist_ok=True)
    portal.META_FILE.write_text('{"campaign":"new-campaign","mode":"new","started_at":9999999999}', encoding="utf-8")
    (root / ".campaign").write_text("old-campaign", encoding="utf-8")
    queue = root / ".input_queue"
    queue.write_text("[Everybody]: Old answer", encoding="utf-8")
    monkeypatch.setattr(portal, "running", lambda: True)

    assert portal.deliver_party_input() is False
    assert queue.read_text(encoding="utf-8") == "[Everybody]: Old answer"


def test_resuming_same_campaign_does_not_clear_its_display(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    calls = []
    expected = str(tmp_path / "campaigns/fresh/session_tail.json")

    def current_display(request, **kwargs):
        calls.append(request)
        return io.BytesIO(json.dumps({"data_root":str(tmp_path), "campaign":"fresh", "tail_path":expected}).encode("utf-8"))

    monkeypatch.setattr(portal.urllib.request, "urlopen", current_display)
    portal.prepare_display("fresh", reset=False)
    assert calls == ["http://127.0.0.1:5001/health"]


@pytest.mark.parametrize("filename", [".input_queue", ".portal-input-claim"])
def test_resume_delivers_reply_owned_by_stopped_session(client, monkeypatch, filename):
    import os
    import time

    folder = portal.DATA_ROOT / "campaigns/fresh"
    folder.mkdir(parents=True)
    (folder / "state.md").write_text("# Fresh\n", encoding="utf-8")
    portal.RUNTIME.mkdir(exist_ok=True)
    previous = time.time() - 20
    portal.META_FILE.write_text(json.dumps({"campaign":"fresh", "provider":"qwen", "started_at":previous}), encoding="utf-8")
    pending = portal.RUNTIME / filename
    pending.write_text("[Everybody]: Keep this reply", encoding="utf-8")
    os.utime(pending, (previous + 1, previous + 1))
    calls = []
    monkeypatch.setattr(portal, "running", lambda: False)
    monkeypatch.setattr(portal, "run", lambda *args, **kwargs:
                        (calls.append(args), subprocess.CompletedProcess(args, 0, "", ""))[1])
    ok, _ = portal.start_session("qwen", "resume", "fresh", "")
    assert ok
    assert portal.read_meta()["started_at"] > previous + 1
    assert portal.read_meta()["input_started_at"] == previous
    monkeypatch.setattr(portal, "running", lambda: True)
    assert portal.deliver_party_input()
    assert any("Keep this reply" in str(args) for args in calls)
    assert not pending.exists()


def test_reply_arriving_during_same_campaign_startup_remains_deliverable(client, monkeypatch):
    import time

    folder = portal.DATA_ROOT / "campaigns/fresh"
    folder.mkdir(parents=True)
    (folder / "state.md").write_text("# Fresh\n", encoding="utf-8")
    portal.RUNTIME.mkdir(exist_ok=True)
    previous = time.time() - 20
    portal.META_FILE.write_text(json.dumps({"campaign":"fresh", "provider":"qwen", "started_at":previous}), encoding="utf-8")
    calls = []

    def startup(*args, **kwargs):
        calls.append(args)
        if "--start" in args:
            (portal.RUNTIME / ".input_queue").write_text("[Everybody]: Arrived while starting", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(portal, "run", startup)
    monkeypatch.setattr(portal, "running", lambda: False)
    assert portal.start_session("qwen", "resume", "fresh", "")[0]
    monkeypatch.setattr(portal, "running", lambda: True)
    assert portal.deliver_party_input()
    assert any("Arrived while starting" in str(args) for args in calls)


@pytest.mark.parametrize("provider", ["qwen", "codex", "claude"])
def test_questionnaire_launch_passes_complete_answers_to_selected_dm(client, monkeypatch, provider):
    import shlex
    calls = []
    monkeypatch.setattr(portal, "CODEX_ENABLED", True)
    monkeypatch.setattr(portal, "running", lambda: False)
    monkeypatch.setattr(portal, "run", lambda *args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    answers = {"ruleset": "2024", "dice": "auto", "tone": "cheerful", "danger": "gentle",
               "audience": "Children aged 3 and 5", "boundaries": "No killing", "starting_level": 2,
               "characters": [{"name": "Silver", "concept": "A dragon monk", "pillar": "Exploring"},
                              {"name": "Magan", "player": "Dad", "details": "A wood elf guide"}]}
    response = api(client, "/api/start", method="post", data={
        "provider": provider, "mode": "new", "name": "mountain-friends", "description": "A golden map", "onboarding": answers})
    assert response.status_code == 200
    assert "from your answers" in response.json["message"]
    command = next(args[-1] for args in calls if args[:2] == ("tmux", "new-session"))
    argv = shlex.split(command)
    assert "DND_ACTIVE_CAMPAIGN=mountain-friends" in argv
    assert provider in argv
    prompt = argv[-1]
    received = json.loads(prompt.split("Completed questionnaire JSON: ", 1)[1])
    assert received == portal.read_onboarding({"onboarding": answers}, "new")
    assert "A golden map" in prompt
    assert "Ask each setup question" not in prompt
    assert "Do not ask routine setup" in prompt


@pytest.mark.parametrize("route", ["/api/start", "/api/switch"])
@pytest.mark.parametrize("answers", [None, [], {"ruleset": 2024}, {"starting_level": True},
    {"starting_level": 21}, {"characters": []}, {"characters": [{}] * 9},
    {"characters": ["Hero"]}, {"characters": [{"concept": {"nested": "bad"}}]},
    {"characters": [{"name": "Silver"}, {"name": "silver"}]},
    {"boundaries": "x" * 1501}, {"unknown": "choice"}])
def test_invalid_questionnaire_never_touches_model_or_display(client, monkeypatch, route, answers):
    monkeypatch.setattr(portal, "running", lambda: pytest.fail("Must validate before model operations"))
    monkeypatch.setattr(portal, "prepare_display", lambda *args, **kwargs: pytest.fail("Must not clear display"))
    monkeypatch.setattr(portal, "run", lambda *args, **kwargs: pytest.fail("Must not call a model"))
    response = api(client, route, method="post", data={"provider": "qwen", "mode": "new", "name": "new-story", "onboarding": answers})
    assert response.status_code == 400
    assert response.json["ok"] is False


def test_questionnaire_defaults_and_legacy_setup_remain_distinct():
    answers = portal.read_onboarding({"onboarding": {}}, "new")
    assert answers["starting_level"] == 1
    assert answers["abilities"] == "pointbuy"
    assert len(answers["characters"]) == 1
    assert portal.read_onboarding({}, "new") is None
    assert "Ask each setup question" in portal.initial_prompt("new", "manual", "")
    assert portal.read_onboarding({"onboarding": {"invalid": []}}, "resume") is None
    assert "Completed questionnaire" not in portal.initial_prompt("resume", "saved", "", answers)


def test_large_questionnaire_is_rejected_before_model_operations(client, monkeypatch):
    monkeypatch.setattr(portal, "running", lambda: pytest.fail("Oversize form must not reach model"))
    response = api(client, "/api/start", method="post", data={"mode": "new", "onboarding": {
        "characters": [{"concept": "x" * 1500, "details": "y" * 1500}] * 8}})
    assert response.status_code == 400
    assert "too long" in response.json["message"]


@pytest.mark.parametrize("mode,onboarding", [("new", None), ("resume", None), ("new", {"characters": [{}]})])
def test_scene_protocol_prefix_reaches_each_launch_mode(mode, onboarding):
    prompt = portal.initial_prompt(mode, "adventure", "A strange new world", onboarding)
    assert prompt.startswith(portal.SCENE_PROTOCOL)
    assert "--scene-key" in prompt
    assert "/health" in prompt
    if onboarding is not None:
        assert json.loads(prompt.split("Completed questionnaire JSON: ", 1)[1]) == onboarding


def test_each_dm_choice_launches_its_own_model():
    sol = portal.model_args("codex-sol", "resume", "Enderal", "", None)
    assert sol[2] == "codex" and sol[sol.index("-m") + 1] == "gpt-6.1-sol"
    terra = portal.model_args("codex", "resume", "Enderal", "", None)
    assert terra[terra.index("-m") + 1] == "gpt-6.1-terra"
    claude = portal.model_args("claude", "resume", "Enderal", "", None)
    assert claude[2] == "claude" and claude[claude.index("--model") + 1] == "opus"


def test_page_lists_every_dm_choice(client):
    page = client[0].get("/", base_url="http://127.0.0.1:5002").get_data(as_text=True)
    for key, choice in portal.DM_CHOICES.items():
        assert f'value="{key}" data-launcher="{choice.launcher}"' in page
    assert page.index('value="codex-sol" data-launcher') < page.index('value="codex" data-launcher')
    assert '<option value="qwen" data-label="Ollama">' in page
