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


def record(args):
    """The tmux call, with the text of any pasted file inline (the file is deleted after the paste)."""
    if args[:2] == ("tmux", "load-buffer"):
        return args + (Path(args[-1]).read_text(encoding="utf-8"),)
    return args


def tmux_recorder(calls, fail=()):
    def fake_run(*args, **kwargs):
        calls.append(record(args))
        return subprocess.CompletedProcess(args, 1 if args[1] in fail else 0, "", "")
    return fake_run


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(portal, "RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(portal, "META_FILE", tmp_path / ".runtime" / "portal-session.json")
    monkeypatch.setattr(portal, "prepare_display", lambda name, **kwargs: None)
    monkeypatch.setattr(portal, "notify_party_consumed", lambda *args: None)
    monkeypatch.setattr(portal.lore_sync, "rt", lambda name: str(tmp_path / ".runtime" / name))
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

    monkeypatch.setattr(portal, "run", tmux_recorder(calls))
    response = api(client, "/api/send", method="post", data={"text": "I choose the forest; no scripts"})
    assert response.status_code == 200
    assert calls[0][:2] == ("tmux", "load-buffer") and calls[0][-1] == "I choose the forest; no scripts"
    assert calls[1][:2] == ("tmux", "paste-buffer") and calls[1][-1] == "dnd-campaign-portal:0.0"
    assert calls[2][1:] == ("send-keys", "-t", "dnd-campaign-portal:0.0", "Enter")


def test_long_input_reaches_the_dm_as_one_bracketed_paste(monkeypatch):
    # Claude Code kept only the last ~105 characters of `send-keys -l` input (#108).
    calls = []
    monkeypatch.setattr(portal, "run", tmux_recorder(calls))
    monkeypatch.setattr(portal.time, "sleep", lambda _: None)
    text = "[[Turn card, hidden from players: " + "Brinewick, the flour cart. " * 80 + ']] Input: "We go."'
    assert len(text) > 2000
    assert portal.submit_to_dm(text) and portal.submit_to_dm("second")
    load, paste, enter, second = calls[:4]
    assert load[-1] == text and second[-1] == "second"
    buffer = load[load.index("-b") + 1]
    assert paste[:2] == ("tmux", "paste-buffer") and {"-p", "-d"} <= set(paste)
    assert paste[paste.index("-b") + 1] == buffer and paste[paste.index("-t") + 1] == "dnd-campaign-portal:0.0"
    assert enter[1:] == ("send-keys", "-t", "dnd-campaign-portal:0.0", "Enter")
    assert second[second.index("-b") + 1] != buffer
    assert not any(Path(args[-2]).exists() for args in calls if args[1] == "load-buffer")
    assert not any("-l" in args for args in calls)


def test_paste_into_a_closed_pane_cleans_up_and_keeps_the_reply(client, monkeypatch):
    portal.RUNTIME.mkdir(parents=True, exist_ok=True)
    portal.META_FILE.write_text('{"campaign":"demo","mode":"resume"}', encoding="utf-8")
    (portal.RUNTIME / ".input_queue").write_text("[Everybody]: Keep me", encoding="utf-8")
    calls = []
    monkeypatch.setattr(portal, "running", lambda: True)
    monkeypatch.setattr(portal, "run", tmux_recorder(calls, fail={"paste-buffer"}))
    assert portal.deliver_party_input() is False
    load = calls[0]
    assert ("tmux", "delete-buffer", "-b", load[load.index("-b") + 1]) in calls
    assert not Path(load[-2]).exists()
    assert not any(args[-1] == "Enter" for args in calls)
    assert "Keep me" in (portal.RUNTIME / ".portal-input-claim").read_text(encoding="utf-8")


def test_paste_timeout_still_removes_the_buffer_and_file(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(record(args))
        if args[1] == "paste-buffer":
            raise subprocess.TimeoutExpired(args, 15)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(portal, "run", fake_run)
    with pytest.raises(subprocess.TimeoutExpired):
        portal.submit_to_dm("hello")
    load = calls[0]
    assert ("tmux", "delete-buffer", "-b", load[load.index("-b") + 1]) in calls
    assert not Path(load[-2]).exists()

def test_codex_can_write_selected_campaign_root(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    args = portal.model_args("codex", "new", "demo", "start")
    assert args[args.index("-m") + 1] == "gpt-6.1-terra"
    assert args[args.index("--add-dir") + 1] == str(tmp_path)
    assert args[args.index("-s") + 1] == "workspace-write"
    assert "sandbox_workspace_write.network_access=true" in args


def test_codex_dm_has_no_screen_control(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    args = portal.model_args("codex-sol", "resume", "demo", "")
    assert args[args.index("--disable") + 1] == "computer_use"
    for setting in ('plugins."computer-use@openai-bundled".enabled=false',
                    'plugins."unified-computer-use@openai-bundled".enabled=false',
                    'mcp_servers.cua_repl={command="true",enabled=false}',
                    'mcp_servers.node_repl={command="true",enabled=false}'):
        assert setting in args
    assert args.index("--disable") < len(args) - 1


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
    monkeypatch.setattr(portal, "run", tmux_recorder(calls))
    monkeypatch.setattr(portal, "notify_party_consumed", lambda: calls.append(("consumed",)))

    assert portal.deliver_party_input() is True
    assert not queue.exists()
    assert any("My name is Enther" in " ".join(args) for args in calls)
    assert any("setup question" in " ".join(args) for args in calls)
    assert not any(portal.SCENE_PROTOCOL in " ".join(args) for args in calls)
    assert calls[-1] == ("consumed",)
    assert portal.deliver_party_input() is False


def play_turn(monkeypatch, mode="resume", campaign="Fenmoor"):
    import shutil
    portal.RUNTIME.mkdir(parents=True, exist_ok=True)
    folder = portal.DATA_ROOT / "campaigns" / campaign
    if not folder.exists():
        shutil.copytree(REPO / "tests/fixtures/fenmoor", folder)
    portal.META_FILE.write_text(json.dumps({"campaign": campaign, "mode": mode}), encoding="utf-8")
    (portal.RUNTIME / ".input_queue").write_text("[Corin Ashby]: We ask Rosa about cargo.", encoding="utf-8")
    calls = []

    def fake_run(*args, **kwargs):
        if any("\x00" in arg for arg in args):
            raise ValueError("embedded null byte")  # as subprocess does
        calls.append(record(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(portal, "running", lambda: True)
    monkeypatch.setattr(portal, "run", fake_run)
    delivered = portal.deliver_party_input()
    typed = [args[-1] for args in calls if args[1] == "load-buffer"]
    return delivered, typed[0] if typed else "", folder


def test_play_turn_carries_the_turn_card_instead_of_scene_protocol(client, monkeypatch):
    delivered, prompt, _ = play_turn(monkeypatch)
    assert delivered
    assert prompt.startswith("[[Turn card, hidden from players: Where: Brinewick")
    assert 'Beat 1b "Broken Ledgers"' in prompt and "Trade policy" in prompt
    assert portal.SCENE_PROTOCOL not in prompt and "--scene-key" not in prompt
    assert "setup question" not in prompt
    assert "\n" not in prompt
    assert prompt.endswith('Input: "[Corin Ashby]: We ask Rosa about cargo."')


def test_broken_state_still_delivers_the_reply(client, monkeypatch):
    folder = portal.DATA_ROOT / "campaigns" / "Fenmoor"
    folder.mkdir(parents=True)
    (folder / "state.md").write_bytes(b"\xff\xfe\x00 not utf-8 ## Campaign Arc ```yaml [")
    delivered, prompt, _ = play_turn(monkeypatch)
    assert delivered and "We ask Rosa about cargo" in prompt


def test_nul_in_state_still_delivers_the_reply(client, monkeypatch):
    import shutil
    folder = portal.DATA_ROOT / "campaigns" / "Fenmoor"
    shutil.copytree(REPO / "tests/fixtures/fenmoor", folder)
    state = folder / "state.md"
    state.write_text(state.read_text(encoding="utf-8").replace("Brinewick, Rosa", "Salt\x00mere, Rosa"), encoding="utf-8")
    delivered, prompt, _ = play_turn(monkeypatch)
    assert delivered and "We ask Rosa about cargo" in prompt


def test_card_failure_still_delivers_the_reply(client, monkeypatch):
    monkeypatch.setattr(portal.turn_card, "prepare", lambda *args: 1 / 0)
    delivered, prompt, _ = play_turn(monkeypatch)
    assert delivered and prompt.startswith("Party Input") and "We ask Rosa about cargo" in prompt


def test_setup_note_stops_once_a_new_campaign_has_characters(client, monkeypatch):
    folder = portal.DATA_ROOT / "campaigns" / "Fresh"
    (folder / "characters").mkdir(parents=True)
    (folder / "state.md").write_text("# Campaign: Fresh\n", encoding="utf-8")
    _, prompt, _ = play_turn(monkeypatch, mode="new", campaign="Fresh")
    assert "send.py --setup-question" in prompt
    (folder / "characters" / "corin.md").write_text("# Corin\n", encoding="utf-8")
    _, prompt, _ = play_turn(monkeypatch, mode="new", campaign="Fresh")
    assert "setup question" not in prompt


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
    monkeypatch.setattr(portal, "run", tmux_recorder(calls))
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
        calls.append(record(args))
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
    assert '--scene "<place>: <what is happening>"' in prompt
    assert len(portal.SCENE_PROTOCOL) < 450
    if onboarding is not None:
        assert json.loads(prompt.split("Completed questionnaire JSON: ", 1)[1]) == onboarding


def test_each_dm_choice_launches_its_own_model():
    sol = portal.model_args("codex-sol", "resume", "Fenmoor", "", None)
    assert sol[2] == "codex" and sol[sol.index("-m") + 1] == "gpt-6.1-sol"
    terra = portal.model_args("codex", "resume", "Fenmoor", "", None)
    assert terra[terra.index("-m") + 1] == "gpt-6.1-terra"
    claude = portal.model_args("claude", "resume", "Fenmoor", "", None)
    assert claude[2] == "claude" and claude[claude.index("--model") + 1] == "opus"


def test_page_lists_every_dm_choice(client):
    page = client[0].get("/", base_url="http://127.0.0.1:5002").get_data(as_text=True)
    for key, choice in portal.DM_CHOICES.items():
        assert f'value="{key}" data-launcher="{choice.launcher}"' in page
    assert page.index('value="codex-sol" data-launcher') < page.index('value="codex" data-launcher')
    assert '<option value="qwen" data-label="Ollama">' in page


# ── DM effort and sidebar notes helper ───────────────────────────────────────

def _effort(argv):
    flags = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-c" and "model_reasoning_effort" in argv[i + 1]]
    assert len(flags) == 1
    return flags[0]


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_codex_dm_runs_at_chosen_effort(effort):
    argv = portal.model_args("codex-sol", "resume", "Fenmoor", "", None, effort)
    assert _effort(argv) == f'model_reasoning_effort="{effort}"'


def test_codex_dm_effort_defaults_to_medium_and_others_get_none():
    assert _effort(portal.model_args("codex", "resume", "Fenmoor", "")) == 'model_reasoning_effort="medium"'
    for provider in ("claude", "qwen"):
        assert not any("model_reasoning_effort" in a for a in portal.model_args(provider, "resume", "Fenmoor", "", None, "low"))
    with pytest.raises(ValueError):
        portal.model_args("codex", "resume", "Fenmoor", "", None, "xhigh")


def test_start_passes_effort_to_launch_and_records_it(client, monkeypatch):
    import shlex
    calls = []
    monkeypatch.setattr(portal, "CODEX_ENABLED", True)
    monkeypatch.setattr(portal, "running", lambda: False)
    monkeypatch.setattr(portal, "run", lambda *args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    response = api(client, "/api/start", method="post", data={
        "provider": "codex-sol", "mode": "new", "name": "quick", "description": "x", "effort": "low"})
    assert response.status_code == 200
    argv = shlex.split(next(args[-1] for args in calls if args[:2] == ("tmux", "new-session")))
    assert _effort(argv) == 'model_reasoning_effort="low"'
    assert json.loads(portal.META_FILE.read_text(encoding="utf-8"))["effort"] == "low"


@pytest.mark.parametrize("route", ["/api/start", "/api/switch"])
@pytest.mark.parametrize("effort", ["xhigh", "", 3, None])
def test_invalid_effort_is_rejected_before_model_operations(client, monkeypatch, route, effort):
    monkeypatch.setattr(portal, "running", lambda: pytest.fail("Must validate before model operations"))
    monkeypatch.setattr(portal, "run", lambda *args, **kwargs: pytest.fail("Must not call a model"))
    response = api(client, route, method="post", data={"provider": "codex", "mode": "new", "name": "x", "effort": effort})
    assert response.status_code == 400


@pytest.mark.parametrize("effort", ["medium", "low"])
def test_resuming_the_running_campaign_always_starts_a_handoff(client, monkeypatch, effort):
    portal.RUNTIME.mkdir(parents=True, exist_ok=True)
    (portal.DATA_ROOT / "campaigns" / "Fenmoor").mkdir(parents=True)
    (portal.DATA_ROOT / "campaigns" / "Fenmoor" / "state.md").write_text("# s", encoding="utf-8")
    portal.META_FILE.write_text(json.dumps({"provider": "codex-sol", "mode": "resume", "campaign": "Fenmoor",
                                            "started_at": 1, "effort": "medium"}), encoding="utf-8")
    monkeypatch.setattr(portal, "CODEX_ENABLED", True)
    monkeypatch.setattr(portal, "running", lambda: True)
    started = []
    monkeypatch.setattr(portal, "tmux_identity", lambda: "$1")
    monkeypatch.setattr(portal, "provider_error", lambda provider: None)
    monkeypatch.setattr(portal.threading, "Thread", lambda target, args, **kw: type(
        "T", (), {"start": lambda self: started.append(args)})())
    body = {"provider": "codex-sol", "mode": "resume", "name": "Fenmoor", "effort": effort}
    response = api(client, "/api/switch", method="post", data=body)
    assert response.status_code == 202
    assert started and started[0][-1] == effort
    portal.set_handoff("idle", "")


def test_helper_choice_defaults_saves_and_is_read_by_lore_sync(client, monkeypatch):
    assert api(client, "/api/state").json["helper"] == "luna"
    for key in ("off", "terra", "haiku", "qwen", "luna"):
        response = api(client, "/api/helper", method="post", data={"helper": key})
        assert response.status_code == 200 and response.json["helper"] == key
        assert api(client, "/api/state").json["helper"] == key
    api(client, "/api/helper", method="post", data={"helper": "haiku"})
    assert json.loads((portal.RUNTIME / "helper-settings.json").read_text(encoding="utf-8")) == {"helper": "haiku"}
    monkeypatch.delenv("DND_LORE_SYNC", raising=False)
    monkeypatch.delenv("DND_LORE_MODEL", raising=False)
    assert portal.lore_sync.choose_model() == ("claude", "haiku")


@pytest.mark.parametrize("value", ["gpt-9", "", None, ["off"]])
def test_unknown_helper_is_rejected_and_keeps_choice(client, value):
    api(client, "/api/helper", method="post", data={"helper": "qwen"})
    assert api(client, "/api/helper", method="post", data={"helper": value}).status_code == 400
    assert api(client, "/api/state").json["helper"] == "qwen"


def test_helper_requires_token_and_origin(client):
    assert api(client, "/api/helper", method="post", data={"helper": "off"}, token=False).status_code == 403
    assert api(client, "/api/helper", method="post", data={"helper": "off"}, origin="http://evil.example").status_code == 403
    assert api(client, "/api/state").json["helper"] == "luna"


def test_page_offers_effort_and_helper_choices(client):
    page = client[0].get("/", base_url="http://127.0.0.1:5002").get_data(as_text=True)
    assert '<option value="medium" selected>' in page
    for key in ("low", "high"):
        assert f'<option value="{key}">' in page
    for key, label in (("off", "Off"), ("luna", "GPT-6 Luna"), ("terra", "GPT-6.1 Terra"),
                       ("haiku", "Claude Haiku"), ("qwen", "Local Qwen")):
        assert f'<option value="{key}">{label}</option>' in page
    assert "Off turns it off" in page


def test_helper_file_follows_runtime_dir_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path / "rt"))
    portal.save_helper("qwen")
    assert json.loads((tmp_path / "rt" / "helper-settings.json").read_text(encoding="utf-8")) == {"helper": "qwen"}
    assert portal.read_helper() == "qwen" and portal.lore_sync.saved_helper() == "qwen"


def test_no_tmux_means_no_dm_running(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("tmux")
    monkeypatch.setattr(portal.subprocess, "run", missing)
    assert portal.running() is False


def test_resumed_dm_reads_the_stop_point_first_and_skips_the_full_logs():
    prompt = portal.initial_prompt("resume", "Fenmoor", "")
    assert "stop_point.py show --campaign Fenmoor" in prompt
    assert "session-log.md" in prompt and "exact moment" in prompt
    assert prompt.index("stop_point.py show") < prompt.index("Open as")


def test_portal_page_offers_save_and_close_and_always_reloads_on_resume(client):
    test_client, _ = client
    page = test_client.get("/", base_url="http://127.0.0.1:5002").data.decode("utf-8")
    assert 'id="save-close"' in page and "Save &amp; close session" in page
    assert "api('/api/close', 'POST'" in page
    assert "data.continuing" not in page and "Save & reload campaign" in page
    assert "handoff.action === 'close'" in page and "closingSession" not in page
