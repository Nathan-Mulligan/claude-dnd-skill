"""Changing DMs never closes the current session before a verified save."""

import importlib.util
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("handoff_portal", REPO / "scripts/campaign_portal.py")
portal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portal)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(portal, "RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(portal, "META_FILE", tmp_path / ".runtime/portal-session.json")
    monkeypatch.setattr(portal, "_handoff", {"status": "idle", "message": ""})
    monkeypatch.setattr(portal, "CODEX_ENABLED", True)
    portal.RUNTIME.mkdir()
    old = tmp_path / "campaigns/old"
    (old / "characters").mkdir(parents=True)
    for name in ("state.md", "session-log.md", "session-tail.md", "characters/Hero.md"):
        (old / name).write_text("Saved campaign\n", encoding="utf-8")
    previous = {"campaign": "old", "provider": "qwen", "mode": "resume", "started_at": 100}
    portal.META_FILE.write_text(json.dumps(previous), encoding="utf-8")
    alive = {"value": True}
    calls = []
    monkeypatch.setattr(portal, "running", lambda: alive["value"])

    def fake_run(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("tmux", "display-message"):
            return subprocess.CompletedProcess(args, 0, "$7\n", "")
        if args[:2] == ("tmux", "kill-session"):
            alive["value"] = False
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(portal, "run", fake_run)
    monkeypatch.setattr(portal, "notify_party_consumed", lambda *args: None)
    monkeypatch.setattr(portal, "notify_display", lambda *args: True)
    app = portal.create_app()
    app.testing = True
    client = app.test_client()
    page = client.get("/", base_url="http://127.0.0.1:5002")
    token = re.search(rb'const token = "([^"]+)";', page.data).group(1).decode()

    def api(path, payload=None, method="post"):
        return getattr(client, method)(path, base_url="http://127.0.0.1:5002", json=payload,
                                      headers={"Origin": "http://127.0.0.1:5002", "X-DND-Portal-Token": token})

    return old, previous, alive, calls, api


def save_and_acknowledge(old, token):
    request, _ = portal.handoff_paths(token)
    pending = json.loads(request.read_text(encoding="utf-8"))
    for name in ("state.md", "session-log.md", "session-tail.md"):
        (old / name).write_text("Fresh campaign checkpoint\n", encoding="utf-8")
        os.utime(old / name, ns=(pending["requested_at_ns"] + 1_000_000, pending["requested_at_ns"] + 1_000_000))
    portal.acknowledge_handoff(token)


def test_switch_starts_background_save_and_blocks_other_input(setup, monkeypatch):
    _, _, _, calls, api = setup
    workers = []

    class Worker:
        def __init__(self, **kwargs):
            workers.append(kwargs)

        def start(self):
            pass

    monkeypatch.setattr(portal.threading, "Thread", Worker)
    response = api("/api/switch", {"provider": "codex", "mode": "resume", "name": "old"})
    assert response.status_code == 202
    assert portal.handoff_state()["status"] == "saving"
    assert len(workers) == 1
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)
    assert api("/api/send", {"text": "Another move"}).status_code == 409
    assert api("/api/start", {"provider": "codex", "mode": "resume", "name": "old"}).status_code == 409
    assert api("/api/switch", {"provider": "codex", "mode": "resume", "name": "old"}).status_code == 409
    assert portal.deliver_party_input() is False
    state = api("/api/state", method="get")
    assert state.json["handoff"]["status"] == "saving"


def test_resuming_with_the_running_dm_saves_then_starts_a_fresh_one(setup, monkeypatch):
    _, previous, _, calls, api = setup
    workers = []

    class Worker:
        def __init__(self, target, args, **kwargs):
            workers.append((target, args))

        def start(self):
            pass

    monkeypatch.setattr(portal.threading, "Thread", Worker)
    response = api("/api/switch", {"provider": "qwen", "mode": "resume", "name": "old"})
    assert response.status_code == 202
    assert response.json["campaign"] == "old"
    assert "continuing" not in response.json
    assert portal.handoff_state()["status"] == "saving"
    assert [(target, args[:5]) for target, args in workers] == [
        (portal.perform_handoff, ("qwen", "resume", "old", "", previous))]
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)


def test_fresh_dm_for_the_same_campaign_restarts_only_after_a_verified_save(setup, monkeypatch):
    old, previous, alive, calls, _ = setup
    token = "b" * 32
    actions = []

    def checkpoint(prompt):
        actions.append("save")
        save_and_acknowledge(old, token)

    def start(provider, mode, name, description, onboarding=None, effort="medium"):
        assert not alive["value"]
        actions.append(("start", provider, mode, name))
        alive["value"] = True
        return True, "Started"

    monkeypatch.setattr(portal, "send_checkpoint_request", checkpoint)
    monkeypatch.setattr(portal, "start_session", start)
    portal.perform_handoff("qwen", "resume", "old", "", previous, "$7", token)
    assert portal.handoff_state()["status"] == "complete"
    assert actions == ["save", ("start", "qwen", "resume", "old")]
    assert ("tmux", "kill-session", "-t", "$7") in calls


def test_resuming_with_the_running_dm_keeps_it_while_a_reply_is_queued(setup, monkeypatch):
    _, _, _, calls, api = setup
    (portal.RUNTIME / ".input_queue").write_text("We open the door\n", encoding="utf-8")
    monkeypatch.setattr(portal.threading, "Thread", lambda **kwargs: pytest.fail("Must not start a handoff"))
    response = api("/api/switch", {"provider": "qwen", "mode": "resume", "name": "old"})
    assert response.status_code == 409
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)
    assert portal.handoff_state()["status"] == "idle"


def test_resuming_with_no_dm_running_starts_one_directly(setup, monkeypatch):
    _, _, alive, calls, api = setup
    alive["value"] = False
    started = []
    monkeypatch.setattr(portal.threading, "Thread", lambda **kwargs: pytest.fail("Nothing to save"))
    monkeypatch.setattr(portal, "start_session", lambda *args: started.append(args[:3]) or (True, "Started"))
    response = api("/api/switch", {"provider": "qwen", "mode": "resume", "name": "old"})
    assert response.status_code == 200
    assert started == [("qwen", "resume", "old")]
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)


def test_invalid_target_does_not_request_a_save(setup):
    _, _, _, calls, api = setup
    response = api("/api/switch", {"provider": "other", "mode": "resume", "name": "old"})
    assert response.status_code == 409
    assert calls == []


def test_verified_checkpoint_precedes_exact_session_stop_and_restart(setup, monkeypatch):
    old, previous, alive, calls, _ = setup
    token = "a" * 32
    actions = []

    def checkpoint(prompt):
        assert "\n" not in prompt and not prompt.startswith("/")
        assert str(old) in prompt and token in prompt
        actions.append("save")
        save_and_acknowledge(old, token)

    def start(provider, mode, name, description, onboarding=None, effort="medium"):
        assert effort == "medium"
        assert not alive["value"]
        assert (provider, mode, name) == ("codex", "resume", "old")
        actions.append("start")
        alive["value"] = True
        return True, "Started"

    monkeypatch.setattr(portal, "send_checkpoint_request", checkpoint)
    monkeypatch.setattr(portal, "start_session", start)
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", token)
    assert portal.handoff_state()["status"] == "complete"
    assert actions == ["save", "start"]
    assert ("tmux", "kill-session", "-t", "$7") in calls
    assert not list((portal.RUNTIME / "handoffs").glob("*.json"))
    assert (old / "characters/Hero.md").read_text(encoding="utf-8") == "Saved campaign\n"


def test_missing_acknowledgement_keeps_original_dm_running(setup, monkeypatch):
    _, previous, alive, calls, _ = setup
    monkeypatch.setattr(portal, "HANDOFF_TIMEOUT", 0)
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: None)
    monkeypatch.setattr(portal, "start_session", lambda *args: pytest.fail("Must not start another DM"))
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", "b" * 32)
    assert portal.handoff_state()["status"] == "failed"
    assert "original DM was not stopped" in portal.handoff_state()["message"]
    assert alive["value"]
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)


def test_stale_campaign_files_cannot_be_acknowledged(setup):
    _, previous, _, _, _ = setup
    token = "c" * 32
    request, ack = portal.handoff_paths(token)
    portal.write_private_json(request, {"token": token, "campaign": "old", "session": list(portal.session_identity(previous)),
                                        "requested_at_ns": time.time_ns() + 1_000_000})
    with pytest.raises(ValueError, match="has not saved a fresh"):
        portal.acknowledge_handoff(token)
    assert not ack.exists()


def test_files_changed_after_acknowledgement_keep_original_running(setup, monkeypatch):
    old, previous, alive, calls, _ = setup
    token = "d" * 32

    def checkpoint(prompt):
        save_and_acknowledge(old, token)
        (old / "characters/Hero.md").write_text("A later edit\n", encoding="utf-8")

    monkeypatch.setattr(portal, "send_checkpoint_request", checkpoint)
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", token)
    assert portal.handoff_state()["status"] == "failed"
    assert alive["value"]
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)


def test_different_tmux_session_is_never_stopped(setup, monkeypatch):
    old, previous, alive, calls, _ = setup
    identities = iter(["$7", "$8"])
    monkeypatch.setattr(portal, "tmux_identity", lambda: next(identities))
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: save_and_acknowledge(old, "e" * 32))
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", "e" * 32)
    assert portal.handoff_state()["status"] == "failed"
    assert alive["value"]
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)


def test_start_failure_after_verified_save_keeps_saved_campaign(setup, monkeypatch):
    old, previous, alive, _, _ = setup
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: save_and_acknowledge(old, "f" * 32))
    monkeypatch.setattr(portal, "start_session", lambda *args: (False, "Provider unavailable"))
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", "f" * 32)
    assert portal.handoff_state()["status"] == "failed"
    assert "campaign is saved" in portal.handoff_state()["message"]
    assert not alive["value"]
    assert (old / "state.md").read_text(encoding="utf-8") == "Fresh campaign checkpoint\n"


def test_symlinked_acknowledgement_is_rejected_without_stopping(setup, monkeypatch, tmp_path):
    _, previous, alive, calls, _ = setup
    token = "1" * 32

    def checkpoint(prompt):
        _, ack = portal.handoff_paths(token)
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        try:
            ack.symlink_to(outside)
        except OSError:
            pytest.skip("Symbolic links not available")

    monkeypatch.setattr(portal, "send_checkpoint_request", checkpoint)
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", token)
    assert portal.handoff_state()["status"] == "failed"
    assert alive["value"]
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)


def test_campaign_symlink_outside_root_is_rejected(setup, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = portal.DATA_ROOT / "campaigns/redirect"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links not available")
    with pytest.raises(ValueError, match="inside the campaign"):
        portal.campaign_path("redirect")


def test_save_request_is_not_submitted_when_composer_probe_fails(setup, monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "An unrelated modal", "")

    monkeypatch.setattr(portal, "run", fake_run)
    monkeypatch.setattr(portal.time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match="not visible"):
        portal.send_checkpoint_request("Save this campaign and wait for the next DM.")
    assert not any(args[-1] == "Enter" for args in calls)


def test_save_request_is_submitted_only_after_visible_probe(setup, monkeypatch):
    calls = []
    prompt = "Save this campaign and wait for the next DM."

    def fake_run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, prompt if args[1] == "capture-pane" else "", "")

    monkeypatch.setattr(portal, "run", fake_run)
    monkeypatch.setattr(portal.time, "sleep", lambda _: None)
    portal.send_checkpoint_request(prompt)
    assert [args[1] for args in calls] == ["load-buffer", "paste-buffer", "capture-pane", "send-keys"]
    assert calls[-1][-1] == "Enter"


def test_save_request_to_a_closed_pane_fails_without_enter(setup, monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1 if args[1] == "paste-buffer" else 0, "", "")

    monkeypatch.setattr(portal, "run", fake_run)
    with pytest.raises(ValueError, match="save"):
        portal.send_checkpoint_request("Save this campaign and wait for the next DM.")
    assert not any(args[-1] == "Enter" for args in calls)
    assert any(args[1] == "delete-buffer" for args in calls)

def test_acknowledgement_cli_checks_files_without_starting_portal(setup):
    old, previous, _, _, _ = setup
    token = "2" * 32
    request, ack = portal.handoff_paths(token)
    portal.write_private_json(request, {"token": token, "campaign": "old", "session": list(portal.session_identity(previous)),
                                        "requested_at_ns": 0})
    env = os.environ.copy()
    env["DND_CAMPAIGN_ROOT"] = str(portal.DATA_ROOT)
    result = subprocess.run([os.sys.executable, str(REPO / "scripts/campaign_portal.py"),
                             "--acknowledge-handoff", token], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    data = json.loads(ack.read_text(encoding="utf-8"))
    assert data["complete"] is True
    assert set(data["files"]) == {"state.md", "session-log.md", "session-tail.md", "characters/Hero.md"}
    assert not list(ack.parent.glob("*.writing"))


def test_pending_party_reply_prevents_switch(setup):
    _, _, alive, calls, api = setup
    (portal.RUNTIME / ".input_queue").write_text("[Everybody]: Please wait", encoding="utf-8")
    response = api("/api/switch", {"provider": "codex", "mode": "resume", "name": "old"})
    assert response.status_code == 409
    assert "queued" in response.json["message"]
    assert alive["value"]
    assert not any(args[1] in {"send-keys", "paste-buffer"} for args in calls)


def test_party_reply_during_save_keeps_original_running(setup, monkeypatch):
    old, previous, alive, calls, _ = setup
    token = "3" * 32

    def checkpoint(prompt):
        save_and_acknowledge(old, token)
        (portal.RUNTIME / ".input_queue").write_text("[Everybody]: A new reply", encoding="utf-8")

    monkeypatch.setattr(portal, "send_checkpoint_request", checkpoint)
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", token)
    assert portal.handoff_state()["status"] == "failed"
    assert alive["value"]
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)
    assert (portal.RUNTIME / ".input_queue").read_text(encoding="utf-8") == "[Everybody]: A new reply"


def test_malformed_switch_body_does_not_change_current_session(setup):
    _, _, alive, calls, api = setup
    assert api("/api/switch", ["codex", "old"]).status_code == 400
    assert alive["value"]
    assert calls == []


def test_missing_target_provider_keeps_original_without_save(setup, monkeypatch):
    _, _, alive, _, api = setup
    monkeypatch.setattr(portal, "provider_error", lambda provider: "Selected DM is not installed")
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: pytest.fail("Must not request a save"))
    response = api("/api/switch", {"provider": "codex", "mode": "resume", "name": "old"})
    assert response.status_code == 409
    assert "not installed" in response.json["message"]
    assert alive["value"]
    assert portal.handoff_state()["status"] == "idle"


def test_input_lease_failure_keeps_original_without_save(setup, monkeypatch):
    _, previous, alive, calls, _ = setup
    monkeypatch.setattr(portal, "notify_display", lambda *args: False)
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: pytest.fail("Must not request a save"))
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", "4" * 32)
    assert portal.handoff_state()["status"] == "failed"
    assert alive["value"]
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)


def test_input_lease_is_renewed_before_stop_and_released_after_start(setup, monkeypatch):
    old, previous, alive, _, _ = setup
    events = []
    token = "5" * 32
    monkeypatch.setattr(portal, "notify_display", lambda route, payload: events.append((route, payload)) or True)
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: save_and_acknowledge(old, token))
    monkeypatch.setattr(portal, "start_session", lambda *args: events.append(("start", {})) or (True, "Started"))
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", token)
    assert portal.handoff_state()["status"] == "complete"
    assert [entry[1].get("active") for entry in events if entry[0] == "/campaign-handoff"] == [True, True, False]
    assert events[-1] == ("/campaign-handoff", {"campaign": "old", "token": token, "active": False})
    assert events[-2][0] == "start"
    assert not alive["value"]


def test_target_provider_lost_during_save_is_not_allowed_to_stop_original(setup, monkeypatch):
    old, previous, alive, calls, _ = setup
    errors = iter([None, "Selected DM disappeared"])
    monkeypatch.setattr(portal, "provider_error", lambda provider: next(errors))
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: save_and_acknowledge(old, "6" * 32))
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", "6" * 32)
    assert portal.handoff_state()["status"] == "failed"
    assert alive["value"]
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)


def test_file_edit_during_final_provider_check_keeps_original_dm(setup, monkeypatch):
    old, previous, alive, calls, _ = setup
    token = "9" * 32
    checks = []

    def preflight(provider):
        checks.append(provider)
        if len(checks) == 2:
            (old / "characters/Hero.md").write_text("Changed during final preflight\n", encoding="utf-8")
        return None

    monkeypatch.setattr(portal, "provider_error", preflight)
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: save_and_acknowledge(old, token))
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", token)
    assert portal.handoff_state()["status"] == "failed"
    assert alive["value"]
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)


def test_tmux_identity_with_a_real_isolated_session(monkeypatch):
    import shlex
    import shutil
    import sys
    import uuid

    tmux = shutil.which("tmux")
    if not tmux:
        pytest.skip("tmux is not installed")
    socket = "dnd-test-" + uuid.uuid4().hex
    session = "proof-session"

    def isolated_run(*args, **kwargs):
        return subprocess.run([tmux, "-L", socket, *args[1:]], capture_output=True, text=True, timeout=10)

    monkeypatch.setattr(portal, "run", isolated_run)
    monkeypatch.setattr(portal, "SESSION", session)
    command = shlex.join([sys.executable, "-c", "import time; time.sleep(30)"])
    started = isolated_run("tmux", "new-session", "-d", "-s", session, command)
    assert started.returncode == 0, started.stderr
    try:
        identity = portal.tmux_identity()
        assert re.fullmatch(r"\$[0-9]+", identity)
    finally:
        isolated_run("tmux", "kill-session", "-t", "=" + session)


def test_new_campaign_questionnaire_survives_saved_handoff(setup, monkeypatch):
    old, previous, alive, calls, api = setup
    answers = {"ruleset": "2024", "dice": "auto", "characters": [{"name": "Silver", "concept": "A friendly dragon"}]}
    expected = portal.read_onboarding({"onboarding": answers}, "new")
    actions = []

    def checkpoint(prompt):
        actions.append("save")
        token = next((portal.RUNTIME / "handoffs").glob("*.request.json")).name.split(".")[0]
        save_and_acknowledge(old, token)

    def start(provider, mode, name, description, onboarding=None, effort="medium"):
        assert effort == "medium"
        assert alive["value"] is False
        assert (provider, mode, name) == ("codex", "new", "dragon-road")
        assert onboarding == expected
        assert description == "A golden map"
        actions.append("start")
        alive["value"] = True
        return True, "Ready"

    class Worker:
        def __init__(self, *, target, args, **kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(portal, "send_checkpoint_request", checkpoint)
    monkeypatch.setattr(portal, "start_session", start)
    monkeypatch.setattr(portal.threading, "Thread", Worker)
    response = api("/api/switch", {"provider": "codex", "mode": "new", "name": "dragon-road",
                                  "description": "A golden map", "onboarding": answers})
    assert response.status_code == 202
    assert portal.handoff_state()["status"] == "complete"
    assert actions == ["save", "start"]
    assert ("tmux", "kill-session", "-t", "$7") in calls


def test_a_save_without_a_fresh_stop_point_cannot_be_acknowledged(setup):
    old, previous, _, _, _ = setup
    token = "7" * 32
    request, ack = portal.handoff_paths(token)
    requested = time.time_ns()
    portal.write_private_json(request, {"token": token, "campaign": "old", "session": list(portal.session_identity(previous)),
                                        "requested_at_ns": requested})
    for name in ("state.md", "session-log.md"):
        os.utime(old / name, ns=(requested + 1_000_000, requested + 1_000_000))
    os.utime(old / "session-tail.md", ns=(requested - 1_000_000, requested - 1_000_000))
    with pytest.raises(ValueError, match="session-tail.md"):
        portal.acknowledge_handoff(token)
    assert not ack.exists()


def test_save_and_close_starts_a_background_wrap_up(setup, monkeypatch):
    _, previous, _, calls, api = setup
    workers = []

    class Worker:
        def __init__(self, target, args, kwargs=None, **rest):
            workers.append((target, args, kwargs or {}))

        def start(self):
            pass

    monkeypatch.setattr(portal.threading, "Thread", Worker)
    response = api("/api/close")
    assert response.status_code == 202
    assert portal.handoff_state()["status"] == "saving"
    assert api("/api/state", method="get").json["handoff"]["action"] == "close"
    [(target, args, kwargs)] = workers
    assert target is portal.perform_handoff
    assert args[:5] == ("qwen", "resume", "old", "", previous) and args[5] == "$7"
    assert kwargs == {"close": True}
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)
    assert api("/api/close").status_code == 409


def test_save_and_close_wraps_up_then_closes_without_a_new_dm(setup, monkeypatch):
    old, previous, alive, calls, _ = setup
    token = "8" * 32
    prompts = []

    def checkpoint(prompt):
        prompts.append(prompt)
        save_and_acknowledge(old, token)

    monkeypatch.setattr(portal, "send_checkpoint_request", checkpoint)
    monkeypatch.setattr(portal, "start_session", lambda *args, **kwargs: pytest.fail("Must not start another DM"))
    monkeypatch.setattr(portal, "provider_error", lambda provider: pytest.fail("Closing needs no provider check"))
    portal.perform_handoff("qwen", "resume", "old", "", previous, "$7", token, close=True)
    state = portal.handoff_state()
    assert state["status"] == "complete" and "closed" in state["message"]
    assert state["action"] == "close"
    assert ("tmux", "kill-session", "-t", "$7") in calls
    assert not alive["value"]
    [prompt] = prompts
    for needle in ("wrap up", "session-log.md", "## Story So Far", "Next Hooks", "Clocks", "stop_point.py write"):
        assert needle in prompt
    assert "\n" not in prompt


def test_save_and_close_keeps_the_dm_when_the_save_is_not_confirmed(setup, monkeypatch):
    _, previous, alive, calls, _ = setup
    monkeypatch.setattr(portal, "CLOSE_TIMEOUT", 0)
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: None)
    portal.perform_handoff("qwen", "resume", "old", "", previous, "$7", "9" * 32, close=True)
    state = portal.handoff_state()
    assert state["status"] == "failed" and state["action"] == "close"
    assert "left running" in state["message"]
    assert alive["value"]
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)


def test_save_and_close_waits_for_a_queued_party_reply(setup, monkeypatch):
    _, _, alive, calls, api = setup
    (portal.RUNTIME / ".input_queue").write_text("[Everybody]: One more thing", encoding="utf-8")
    monkeypatch.setattr(portal.threading, "Thread", lambda **kwargs: pytest.fail("Must not start a wrap-up"))
    response = api("/api/close")
    assert response.status_code == 409
    assert "queued" in response.json["message"]
    assert alive["value"]
    assert all(args[:2] == ("tmux", "display-message") for args in calls)
    assert portal.handoff_state()["status"] == "idle"


def test_save_and_close_with_nothing_running_does_nothing(setup, monkeypatch):
    _, _, alive, calls, api = setup
    alive["value"] = False
    monkeypatch.setattr(portal.threading, "Thread", lambda **kwargs: pytest.fail("Nothing to close"))
    response = api("/api/close")
    assert response.status_code == 409
    assert calls == []
    assert portal.handoff_state()["status"] == "idle"


def stop_voice_calls(calls):
    return [args for args in calls if args[-1:] == ("--stop-voice",)]


def test_save_and_close_stops_the_voice_after_the_dm_closes(setup, monkeypatch):
    old, previous, _, calls, _ = setup
    token = "c" * 32
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: save_and_acknowledge(old, token))
    portal.perform_handoff("qwen", "resume", "old", "", previous, "$7", token, close=True)
    assert portal.handoff_state()["status"] == "complete"
    [stop] = stop_voice_calls(calls)
    assert stop == ("bash", str(portal.REPO / "scripts/launch-campaign-m5.sh"), "--stop-voice")
    assert calls.index(stop) > calls.index(("tmux", "kill-session", "-t", "$7"))


@pytest.mark.parametrize("failure", ["exit", "silent", "timeout", "missing"])
def test_save_and_close_still_closes_when_the_voice_will_not_stop(setup, monkeypatch, capsys, failure):
    old, previous, alive, calls, _ = setup
    token = "d" * 32
    original = portal.run

    def flaky_run(*args, **kwargs):
        if args[-1:] == ("--stop-voice",):
            calls.append(args)
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 30)
            if failure == "missing":
                raise FileNotFoundError("bash")
            if failure == "silent":
                return subprocess.CompletedProcess(args, -9, "", "")
            return subprocess.CompletedProcess(args, 1, "", "Kokoro could not be stopped.")
        return original(*args, **kwargs)

    monkeypatch.setattr(portal, "run", flaky_run)
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: save_and_acknowledge(old, token))
    portal.perform_handoff("qwen", "resume", "old", "", previous, "$7", token, close=True)
    state = portal.handoff_state()
    assert state["status"] == "complete" and "closed" in state["message"]
    assert not alive["value"]
    assert len(stop_voice_calls(calls)) == 1
    assert "Kokoro was not stopped" in capsys.readouterr().out


def test_a_fresh_dm_keeps_the_voice_running(setup, monkeypatch):
    old, previous, alive, calls, _ = setup
    token = "e" * 32
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: save_and_acknowledge(old, token))
    monkeypatch.setattr(portal, "start_session", lambda *args, **kwargs: alive.update(value=True) or (True, "Started"))
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", token)
    assert portal.handoff_state()["status"] == "complete"
    assert ("tmux", "kill-session", "-t", "$7") in calls
    assert stop_voice_calls(calls) == []


def test_a_close_that_never_saved_keeps_the_voice_running(setup, monkeypatch):
    _, previous, _, calls, _ = setup
    monkeypatch.setattr(portal, "CLOSE_TIMEOUT", 0)
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: None)
    portal.perform_handoff("qwen", "resume", "old", "", previous, "$7", "f" * 32, close=True)
    assert portal.handoff_state()["status"] == "failed"
    assert stop_voice_calls(calls) == []


# ── Save & close with the notes helper writing the summary ──────────────────

def save_fields_and_acknowledge(old, token, names=("state.md", "session-tail.md")):
    request, _ = portal.handoff_paths(token)
    pending = json.loads(request.read_text(encoding="utf-8"))
    for name in names:
        (old / name).write_text(f"Fresh {name}\n", encoding="utf-8")
        os.utime(old / name, ns=(pending["requested_at_ns"] + 1_000_000, pending["requested_at_ns"] + 1_000_000))
    portal.acknowledge_handoff(token)


def helper_on(monkeypatch, prepare):
    events = []
    monkeypatch.setattr(portal.session_summary, "helper", lambda: ("codex", "gpt-6-luna"))
    monkeypatch.setattr(portal.session_summary, "prepare",
                        lambda folder, mark, choice: events.append(("prepare", mark, choice)) or prepare())
    monkeypatch.setattr(portal.session_summary, "write", lambda folder, plan: events.append(("write", plan)))
    return events


def test_save_and_close_leaves_the_summary_to_the_helper(setup, monkeypatch):
    old, previous, alive, calls, _ = setup
    token = "1" * 32
    mark = "ab" * 8
    (old / "session-tail.md").write_text(f"# Where we stopped\nOld note\n<!-- display-mark: {mark} -->\n", encoding="utf-8")
    events = helper_on(monkeypatch, lambda: {"session-log.md": ("old", "new")})
    prompts = []

    def checkpoint(prompt):
        prompts.append(prompt)
        save_fields_and_acknowledge(old, token)  # session-log.md is left to the helper

    original_run = portal.run

    def recording_run(*args, **kwargs):
        if args[:2] == ("tmux", "kill-session"):
            events.append(("kill",))
        return original_run(*args, **kwargs)

    monkeypatch.setattr(portal, "run", recording_run)
    monkeypatch.setattr(portal, "send_checkpoint_request", checkpoint)
    portal.perform_handoff("qwen", "resume", "old", "", previous, "$7", token, close=True)
    state = portal.handoff_state()
    assert state["status"] == "complete" and "closed" in state["message"], state
    assert not alive["value"]
    [prompt] = prompts
    for needle in ("wrap up", "notes helper", "Next Hooks", "Clocks", "stop_point.py write"):
        assert needle in prompt
    assert "Write this session's entry" not in prompt
    assert "\n" not in prompt
    assert events == [("prepare", mark, ("codex", "gpt-6-luna")), ("kill",),
                      ("write", {"session-log.md": ("old", "new")})]


def test_a_failed_helper_summary_falls_back_to_the_dm(setup, monkeypatch):
    old, previous, alive, calls, _ = setup
    token = "2" * 32

    def broken():
        raise ValueError("helper reply names what the material never mentions: Veyra")

    events = helper_on(monkeypatch, broken)
    prompts = []

    def checkpoint(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            save_fields_and_acknowledge(old, token)
        else:
            save_fields_and_acknowledge(old, token, names=("session-log.md",))

    monkeypatch.setattr(portal, "send_checkpoint_request", checkpoint)
    portal.perform_handoff("qwen", "resume", "old", "", previous, "$7", token, close=True)
    state = portal.handoff_state()
    assert state["status"] == "complete", state
    assert "DM wrote the summary" in state["message"]
    assert not alive["value"]
    assert len(prompts) == 2
    for needle in ("could not write", "session-log.md", "### Recap", "## Story So Far", "Continuity Archive"):
        assert needle in prompts[1]
    assert "\n" not in prompts[1]
    assert not any(event[0] == "write" for event in events)
    log = Path(portal.session_summary.rt("session_summary.log")).read_text(encoding="utf-8")
    assert "old: helper summary failed" in log and "Veyra" in log


def test_the_dm_must_save_the_log_itself_after_a_helper_failure(setup, monkeypatch):
    old, previous, alive, calls, _ = setup
    token = "3" * 32

    def broken():
        raise RuntimeError("codex timed out")

    helper_on(monkeypatch, broken)
    monkeypatch.setattr(portal, "CLOSE_TIMEOUT", 1)

    def checkpoint(prompt):
        if "notes helper could not" not in prompt:
            return save_fields_and_acknowledge(old, token)
        with pytest.raises(ValueError, match="session-log.md"):
            portal.acknowledge_handoff(token)  # the DM acknowledged without writing the summary

    monkeypatch.setattr(portal, "send_checkpoint_request", checkpoint)
    portal.perform_handoff("qwen", "resume", "old", "", previous, "$7", token, close=True)
    state = portal.handoff_state()
    assert state["status"] == "failed" and "left running" in state["message"], state
    assert alive["value"]
    assert not any(args[:2] == ("tmux", "kill-session") for args in calls)


def test_with_the_helper_off_the_dm_writes_the_summary(setup, monkeypatch):
    old, previous, _, _, _ = setup
    token = "4" * 32
    monkeypatch.setattr(portal.session_summary, "helper", lambda: None)
    monkeypatch.setattr(portal.session_summary, "prepare", lambda *args: pytest.fail("Helper is off"))
    prompts = []
    monkeypatch.setattr(portal, "send_checkpoint_request",
                        lambda prompt: prompts.append(prompt) or save_and_acknowledge(old, token))
    portal.perform_handoff("qwen", "resume", "old", "", previous, "$7", token, close=True)
    assert portal.handoff_state()["status"] == "complete"
    [prompt] = prompts
    assert "Write this session's entry in session-log.md with its Recap" in prompt


def test_a_model_switch_never_uses_the_helper(setup, monkeypatch):
    old, previous, alive, _, _ = setup
    token = "5" * 32
    monkeypatch.setattr(portal.session_summary, "helper", lambda: pytest.fail("Only Save & close summarises"))
    monkeypatch.setattr(portal, "send_checkpoint_request", lambda prompt: save_and_acknowledge(old, token))
    monkeypatch.setattr(portal, "start_session", lambda *args, **kwargs: alive.update(value=True) or (True, "Started"))
    portal.perform_handoff("codex", "resume", "old", "", previous, "$7", token)
    assert portal.handoff_state()["status"] == "complete"
