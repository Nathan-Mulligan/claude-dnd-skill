"""Only the owner drives the portal and picks the display's campaign; the DM can't act as the owner (#139)."""

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DISPLAY = REPO / "skills/dnd/display"
SCRIPTS = REPO / "skills/dnd/scripts"
BASE = "http://127.0.0.1:5002"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


portal = load("_owner_portal", REPO / "scripts/campaign_portal.py")
sys.path.insert(0, str(SCRIPTS))
import paths  # noqa: E402


# ── Portal: a session cookie from a one-time link, never a key in the page ──────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(portal, "RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(portal, "META_FILE", tmp_path / ".runtime" / "portal-session.json")
    monkeypatch.setattr(portal, "running", lambda: False)
    monkeypatch.setattr(portal.lore_sync, "rt", lambda name: str(tmp_path / ".runtime" / name))
    application = portal.create_app()
    application.testing = True
    return application


def link_path(app):
    return urllib.parse.urlsplit(app.config["NEW_LOGIN_LINK"]()).path


def sign_in(app, client):
    """Redeem a link as Safari would; returns the key the page keeps in its storage."""
    response = client.get(link_path(app), base_url=BASE)
    assert response.status_code == 200
    match = re.search(r'localStorage.setItem\("dnd-portal-session", ("[^"]+")\);location.replace\(\'/\'\)',
                      response.get_data(as_text=True))
    return json.loads(match.group(1)), response


def state(client, key=None):
    headers = {portal.SESSION_HEADER: key} if key is not None else {}
    return client.get("/api/state", base_url=BASE, headers=headers)


OWNER_ROUTES = [("get", "/api/state"), ("post", "/api/helper"), ("post", "/api/start"),
                ("post", "/api/switch"), ("post", "/api/close"), ("post", "/api/send")]


def test_the_page_holds_no_key_to_the_api(app):
    client = app.test_client()
    key, _ = sign_in(app, client)
    page = client.get("/", base_url=BASE).get_data(as_text=True)
    assert key not in page and "X-DND-Portal-Token" not in page
    assert '"X-DND-Portal-Session"' in page and "localStorage.getItem" in page


@pytest.mark.parametrize(("method", "route"), OWNER_ROUTES)
def test_every_api_route_refuses_a_caller_without_the_key(app, monkeypatch, method, route):
    monkeypatch.setattr(portal, "run", lambda *a, **k: pytest.fail("must not reach tmux"))
    client = app.test_client()
    for headers in ({}, {portal.SESSION_HEADER: "guess"}, {"X-DND-Portal-Token": "anything"}):
        response = getattr(client, method)(route, base_url=BASE, json={"text": "hi", "provider": "codex"},
                                           headers={"Origin": BASE, **headers})
        assert response.status_code == 403
        assert response.json["message"] == portal.NOT_SIGNED_IN
    assert "start-campaign-portal.sh --restart" in portal.NOT_SIGNED_IN


def test_signing_in_sets_no_cookie_so_no_other_port_ever_receives_the_key(app):
    client = app.test_client()
    key, response = sign_in(app, client)
    assert "Set-Cookie" not in response.headers
    assert response.headers["Cache-Control"] == "no-store" and response.headers["Referrer-Policy"] == "no-referrer"
    assert state(client).status_code == 403
    assert state(client, key).status_code == 200


def test_the_sign_in_link_works_once(app):
    path = link_path(app)
    assert app.test_client().get(path, base_url=BASE).status_code == 200
    response = app.test_client().get(path, base_url=BASE)
    assert response.status_code == 403 and "dnd-portal-session" not in response.get_data(as_text=True)
    assert "--restart" in response.get_data(as_text=True)


def test_the_sign_in_link_runs_out(app, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(portal.time, "monotonic", lambda: now[0])
    path = link_path(app)
    now[0] += portal.LOGIN_TTL + 1
    assert app.test_client().get(path, base_url=BASE).status_code == 403


def test_a_new_link_cancels_the_old_one(app):
    old = link_path(app)
    new = link_path(app)
    assert app.test_client().get(old, base_url=BASE).status_code == 403
    assert app.test_client().get(new, base_url=BASE).status_code == 200


def test_a_wrong_guess_neither_signs_in_nor_spoils_the_real_link(app):
    path = link_path(app)
    for guess in ("/login/wrong", "/login/" + "x" * 43, "/login/%C3%A9"):
        assert app.test_client().get(guess, base_url=BASE).status_code == 403
    assert app.test_client().get(path, base_url=BASE).status_code == 200


def test_a_signed_in_browser_still_needs_the_portal_origin(app, monkeypatch):
    monkeypatch.setattr(portal, "run", lambda *a, **k: pytest.fail("must not reach tmux"))
    client = app.test_client()
    key, _ = sign_in(app, client)
    for origin in ("http://127.0.0.1:5001", "http://127.0.0.1:9999", None):
        headers = {portal.SESSION_HEADER: key, **({"Origin": origin} if origin else {})}
        response = client.post("/api/helper", base_url=BASE, json={"helper": "off"}, headers=headers)
        assert response.status_code == 403


def test_the_owner_routes_are_only_under_api(app):
    public = sorted(str(rule) for rule in app.url_map.iter_rules() if not str(rule).startswith("/api/"))
    assert public == ["/", "/health", "/icon.png", "/login/<code>", "/static/<path:filename>"]


def test_health_reports_a_save_in_progress(app, monkeypatch):
    client = app.test_client()
    assert client.get("/health", base_url=BASE).json["busy"] is False
    monkeypatch.setattr(portal, "_handoff", {"status": "saving", "message": ""})
    assert client.get("/health", base_url=BASE).json["busy"] is True


def test_the_link_goes_to_safari_and_never_to_the_log(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(portal.sys, "platform", "darwin")
    monkeypatch.setattr(portal.sys.stdout, "isatty", lambda: False)
    monkeypatch.delenv("DND_PORTAL_OPEN_BROWSER", raising=False)
    monkeypatch.setattr(portal.subprocess, "run",
                        lambda args, **kwargs: calls.append(args) or subprocess.CompletedProcess(args, 0))
    assert portal.hand_out_login("http://127.0.0.1:5002/login/secret-code") is True
    assert calls == [["open", "-a", "Safari", "http://127.0.0.1:5002/login/secret-code"]]
    out = capsys.readouterr().out
    assert "secret-code" not in out and portal.NO_LINK not in out


def test_the_link_is_printed_only_to_a_terminal(monkeypatch, capsys):
    monkeypatch.setenv("DND_PORTAL_OPEN_BROWSER", "0")
    monkeypatch.setattr(portal.subprocess, "run", lambda *a, **k: pytest.fail("must not open Safari"))
    monkeypatch.setattr(portal.sys.stdout, "isatty", lambda: True)
    assert portal.hand_out_login("http://127.0.0.1:5002/login/secret-code") is True
    assert "secret-code" in capsys.readouterr().out


@pytest.mark.parametrize("safari", ["off", "fails"])
def test_a_link_nobody_received_is_reported(monkeypatch, capsys, safari):
    monkeypatch.setattr(portal.sys, "platform", "darwin")
    monkeypatch.setattr(portal.sys.stdout, "isatty", lambda: False)
    monkeypatch.setenv("DND_PORTAL_OPEN_BROWSER", "0" if safari == "off" else "1")
    monkeypatch.setattr(portal.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 1))
    assert portal.hand_out_login("http://127.0.0.1:5002/login/secret-code") is False
    out = capsys.readouterr().out
    assert portal.NO_LINK in out and "--foreground" in out and "secret-code" not in out


@pytest.mark.skipif(os.name == "nt", reason="the launcher is a bash script")
def test_the_launcher_fails_when_no_sign_in_link_was_handed_out(tmp_path):
    import socket
    data = tmp_path / "data"
    data.mkdir()
    state = tmp_path / "curl-state"
    health = json.dumps({"service": "dnd-campaign-portal", "ready": True, "data_root": str(data)})
    bin_dir = tmp_path / "bin"
    venv = tmp_path / "venv" / "bin"
    for folder, name, body in (
            (bin_dir, "curl", f'[ -e "{state}" ] && {{ printf %s \'{health}\'; exit 0; }}\ntouch "{state}"; exit 1\n'),
            (venv, "python3", f'[ "$1" = -c ] && exit 0\necho "{portal.NO_LINK}: Safari did not open."\n')):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text("#!/bin/sh\n" + body, encoding="utf-8")
        (folder / name).chmod(0o755)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, DND_CAMPAIGN_ROOT=str(data), DND_VENV=str(venv.parent), DND_PORTAL_BOOT_SERVICES="0",
               DND_PORTAL_PORT=str(port), PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(["bash", str(REPO / "scripts/start-campaign-portal.sh")], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1 and portal.NO_LINK in result.stderr


# ── Portal: it records the chosen campaign before the display selects it ──────

def test_starting_a_dm_records_the_chosen_campaign_first(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(portal, "RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(portal, "META_FILE", tmp_path / ".runtime" / "portal-session.json")
    monkeypatch.setattr(portal, "running", lambda: False)
    monkeypatch.setattr(portal, "CODEX_ENABLED", True)
    monkeypatch.setattr(portal, "notify_party_consumed", lambda *args: None)
    chosen = tmp_path / paths.CHOSEN_CAMPAIGN
    seen = []
    monkeypatch.setattr(portal, "prepare_display",
                        lambda name, **kwargs: seen.append(chosen.read_text(encoding="utf-8")))
    calls = []
    monkeypatch.setattr(portal, "run", lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    ok, message = portal.start_session("codex", "new", "fresh", "a story")
    assert ok, message
    assert seen == ["fresh"]
    argv = shlex.split(next(args[-1] for args in calls if args[:2] == ("tmux", "new-session")))
    assert "DND_PORTAL_LAUNCH=1" in argv


# ── The chosen campaign wins over the DM-writable runtime .campaign ───────────

@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path / ".runtime"))
    (tmp_path / ".runtime").mkdir()
    for name in ("Chosen", "Other"):
        (tmp_path / "campaigns" / name).mkdir(parents=True)
        (tmp_path / "campaigns" / name / "state.md").write_text(f"# {name}\n", encoding="utf-8")
    return tmp_path


def test_choose_campaign_records_and_clears(root):
    assert paths.chosen_campaign() is None
    paths.choose_campaign("Chosen")
    assert (root / paths.CHOSEN_CAMPAIGN).read_text(encoding="utf-8") == "Chosen"
    assert paths.chosen_campaign() == "Chosen"
    paths.choose_campaign(None)
    assert paths.chosen_campaign() is None
    for bad in ("../x", ".", "a/b", " spaced"):
        with pytest.raises(ValueError):
            paths.choose_campaign(bad)


def test_agreed_campaign_follows_the_runtime_file_only_when_nobody_chose(root):
    assert paths.agreed_campaign("Other\n") == "Other"
    assert paths.agreed_campaign("../escape") is None
    paths.choose_campaign("Chosen")
    assert paths.agreed_campaign("Chosen") == "Chosen"
    assert paths.agreed_campaign("Other") is None


@pytest.fixture
def display(root, monkeypatch):
    monkeypatch.syspath_prepend(str(DISPLAY))
    import runtime_paths
    monkeypatch.setattr(runtime_paths, "RT", str(root / ".runtime"))
    spec = importlib.util.spec_from_file_location("_owner_display", DISPLAY / "dnd-display-app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.app.testing = True
    module._broadcast = lambda payload: None
    return module, module.app.test_client(), root


def test_the_display_refuses_a_campaign_the_portal_did_not_choose(display):
    module, client, root = display
    paths.choose_campaign("Chosen")
    assert client.post("/stats", json={"campaign": "Chosen"}).status_code == 204
    response = client.post("/stats", json={"campaign": "Other",
                                           "players": [{"name": "Stray", "hp": {"current": 1, "max": 1}}]})
    assert response.status_code == 409 and "Chosen" in response.get_data(as_text=True)
    assert client.get("/health").json["campaign"] == "Chosen"
    other = root / "campaigns" / "Other"
    assert not (other / "party_stats.json").exists() and not (other / "session_tail.json").exists()
    assert (other / "state.md").read_text(encoding="utf-8") == "# Other\n"
    assert (root / ".runtime" / ".campaign").read_text(encoding="utf-8") == "Chosen"


def test_the_display_ignores_a_runtime_campaign_the_dm_wrote(display):
    module, client, root = display
    paths.choose_campaign("Chosen")
    assert client.post("/stats", json={"campaign": "Chosen"}).status_code == 204
    (root / ".runtime" / ".campaign").write_text("Other", encoding="utf-8")
    assert module._active_campaign_name() is None
    health = client.get("/health").json
    assert health["campaign"] == "" and health["tail_path"] == ""
    client.post("/stats", json={"players": [{"name": "Stray", "hp": {"current": 1, "max": 1}}]})
    client.post("/chunk", json={"text": "A line for the wrong table."})
    other = root / "campaigns" / "Other"
    assert not (other / "party_stats.json").exists() and not (other / "session_tail.json").exists()
    assert client.post("/stats", json={"campaign": "Chosen"}).status_code == 204
    assert client.get("/health").json["campaign"] == "Chosen"


def test_without_a_choice_the_display_follows_its_runtime_campaign(display):
    _, client, _ = display
    assert client.post("/stats", json={"campaign": "Other"}).status_code == 204
    assert client.get("/health").json["campaign"] == "Other"


def test_records_and_helpers_follow_the_chosen_campaign(root, monkeypatch):
    monkeypatch.syspath_prepend(str(DISPLAY))
    records = load("_owner_records", DISPLAY / "records.py")
    inventory = load("_owner_inventory", SCRIPTS / "inventory.py")
    autosave = load("_owner_autosave", SCRIPTS / "autosave_checkpoint.py")
    (root / ".runtime" / ".campaign").write_text("Other", encoding="utf-8")
    monkeypatch.setenv("DND_ACTIVE_CAMPAIGN", "Other")
    assert records.campaign_folder() == root / "campaigns" / "Other"
    paths.choose_campaign("Chosen")
    assert records.campaign_folder() == root / "campaigns" / "Chosen"
    assert inventory._display_campaign() == ""
    (root / ".runtime" / "active-campaign.json").write_text(json.dumps({"name": "Other"}), encoding="utf-8")
    assert autosave.active_campaign() == "Chosen"


@pytest.mark.skipif(os.name == "nt", reason="the launcher is a bash script")
@pytest.mark.parametrize(("args", "portal_launch", "expected"), [
    (["--campaign", "Chosen"], "", "Chosen"),
    ([], "", None),
    ([], "1", "Kept"),
])
def test_play_local_records_the_campaign_it_was_given(root, args, portal_launch, expected):
    paths.choose_campaign("Kept")
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "codex").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", DND_PORTAL_LAUNCH=portal_launch)
    env.pop("DND_ACTIVE_CAMPAIGN", None)
    result = subprocess.run(["bash", "scripts/play-local.sh", "codex", *args], cwd=REPO, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert paths.chosen_campaign() == expected
