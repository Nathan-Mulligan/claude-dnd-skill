"""The DM writes only its campaign and the runtime dir; nothing outside its sandbox writes through its links (#135)."""

import importlib.util
import json
import os
import shlex
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DISPLAY = REPO / "skills/dnd/display"
sys.path.insert(0, str(DISPLAY))
import runtime_paths  # noqa: E402

posix_only = pytest.mark.skipif(os.name == "nt", reason="links and the sandboxed launch are POSIX-only")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


portal = load("_write_limits_portal", REPO / "scripts/campaign_portal.py")


def outside_file(tmp_path, text="keep me"):
    target = tmp_path / "outside" / "precious.txt"
    target.parent.mkdir(exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


# ── Writers used outside the sandbox ──────────────────────────────────────────

@posix_only
def test_write_file_replaces_a_planted_link_instead_of_following_it(tmp_path):
    target = outside_file(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "stats.json").symlink_to(target)
    runtime_paths.write_file(runtime / "stats.json", "{}")
    assert target.read_text(encoding="utf-8") == "keep me"
    assert not (runtime / "stats.json").is_symlink()
    assert (runtime / "stats.json").read_text(encoding="utf-8") == "{}"
    assert [p.name for p in runtime.iterdir()] == ["stats.json"]


@posix_only
def test_write_file_refuses_a_linked_folder_below_its_root(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "music").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(OSError):
        runtime_paths.write_file(runtime / "music" / "current.json", "{}", root=runtime)
    assert list(elsewhere.iterdir()) == []


def test_write_file_refuses_a_path_that_climbs_out_of_its_root(tmp_path):
    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    with pytest.raises(OSError):
        runtime_paths.write_file(campaigns / ".." / "escaped" / "cast.json", "{}", root=campaigns)
    assert not (tmp_path / "escaped").exists()


@posix_only
def test_append_text_never_reaches_a_linked_or_hard_linked_file(tmp_path):
    target = outside_file(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / ".input_queue").symlink_to(target)
    with pytest.raises(OSError):
        runtime_paths.append_text(runtime / ".input_queue", "echo pwned")
    os.link(target, runtime / "client-errors.log")
    with pytest.raises(OSError):
        runtime_paths.append_text(runtime / "client-errors.log", "echo pwned")
    assert target.read_text(encoding="utf-8") == "keep me"
    runtime_paths.append_text(runtime / "fresh.log", "one")
    runtime_paths.append_text(runtime / "fresh.log", "two", separator="\n")
    assert (runtime / "fresh.log").read_text(encoding="utf-8") == "one\ntwo"


# ── How the portal starts each DM ─────────────────────────────────────────────

def flag(args, name):
    return [args[i + 1] for i, value in enumerate(args) if value == name]


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(portal, "RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(portal, "META_FILE", tmp_path / ".runtime" / "portal-session.json")
    monkeypatch.setattr(portal, "QWEN_ENABLED", True)
    return tmp_path


@pytest.mark.parametrize("provider", ["codex", "codex-sol"])
def test_codex_dm_runs_in_its_campaign_and_may_write_only_there_and_runtime(data_root, provider):
    args = portal.model_args(provider, "resume", "demo", "")
    assert flag(args, "--workdir") == [str(data_root / "campaigns" / "demo")]
    assert flag(args, "--add-dir") == [str(data_root / ".runtime")]
    assert flag(args, "-s") == ["workspace-write"]
    assert "sandbox_workspace_write.network_access=true" in args
    # The DM can never ask to leave the sandbox: an approval prompt could be answered by party input.
    assert flag(args, "-a") == ["never"]


@pytest.mark.parametrize("provider", ["claude", "claude-sonnet", "claude-fable"])
def test_claude_dm_is_sandboxed_to_its_campaign_and_runtime(data_root, provider):
    args = portal.model_args(provider, "resume", "demo", "")
    assert flag(args, "--workdir") == [str(data_root / "campaigns" / "demo")]
    assert flag(args, "--add-dir") == [str(data_root / ".runtime")]
    assert flag(args, "--permission-mode") == ["acceptEdits"]
    # Settings, hooks and MCP servers planted in the campaign folder are never loaded.
    assert flag(args, "--setting-sources") == ["user"]
    assert "--strict-mcp-config" in args
    settings = json.loads(flag(args, "--settings")[0])
    sandbox = settings["sandbox"]
    assert sandbox["enabled"] is True and sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False and sandbox["autoAllowBashIfSandboxed"] is True
    assert sandbox["network"] == {"allowedDomains": ["*"], "allowLocalBinding": True}
    assert not sandbox.get("excludedCommands")
    assert not sandbox.get("filesystem")
    assert settings["permissions"]["blockWritesOutsideWorkingDirectories"] is True
    assert settings["permissions"]["allow"] == [f"Read(/{portal.REPO}/**)"]


def test_qwen_dm_launch_is_unchanged(data_root):
    args = portal.model_args("qwen", "resume", "demo", "")
    assert "--workdir" not in args and "--add-dir" not in args


def test_dm_prompt_names_the_checkout_and_the_only_writable_folders(data_root):
    prompt = portal.initial_prompt("resume", "demo", "")
    assert f"{portal.REPO}/AGENTS.md" in prompt
    assert f"{portal.REPO}/skills/dnd/DM-CORE.md" in prompt
    assert str(data_root / "campaigns" / "demo") in prompt
    assert str(data_root / ".runtime") in prompt


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_new_campaign_folder_exists_before_its_dm_starts(data_root, monkeypatch, provider):
    calls = []
    monkeypatch.setattr(portal, "CODEX_ENABLED", True)
    monkeypatch.setattr(portal, "running", lambda: False)
    monkeypatch.setattr(portal, "prepare_display", lambda name, **kwargs: None)
    monkeypatch.setattr(portal, "notify_party_consumed", lambda *args: None)

    def fake_run(*args, **kwargs):
        if args[:2] == ("tmux", "new-session"):
            assert (data_root / "campaigns" / "fresh").is_dir()
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(portal, "run", fake_run)
    ok, message = portal.start_session(provider, "new", "fresh", "a story")
    assert ok, message
    argv = shlex.split(next(args[-1] for args in calls if args[:2] == ("tmux", "new-session")))
    assert argv[argv.index("--workdir") + 1] == str(data_root / "campaigns" / "fresh")


@pytest.mark.parametrize("venv", ["", "/opt/dnd-venv"])
def test_dm_uses_the_portals_chosen_venv(data_root, monkeypatch, venv):
    calls = []
    monkeypatch.setenv("DND_VENV", venv)
    monkeypatch.setattr(portal, "running", lambda: False)
    monkeypatch.setattr(portal, "prepare_display", lambda name, **kwargs: None)
    monkeypatch.setattr(portal, "notify_party_consumed", lambda *args: None)
    monkeypatch.setattr(portal, "run", lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    ok, message = portal.start_session("qwen", "new", "fresh", "a story")
    assert ok, message
    argv = shlex.split(next(args[-1] for args in calls if args[:2] == ("tmux", "new-session")))
    assert [a for a in argv if a.startswith("DND_VENV=")] == ([f"DND_VENV={venv}"] if venv else [])


@posix_only
def test_new_campaign_refuses_a_linked_campaign_folder(data_root, monkeypatch):
    elsewhere = data_root / "elsewhere"
    elsewhere.mkdir()
    (data_root / "campaigns").mkdir()
    (data_root / "campaigns" / "fresh").symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setattr(portal, "run", lambda *a, **k: pytest.fail("must not start anything"))
    ok, _ = portal.start_session("codex", "new", "fresh", "a story")
    assert not ok


@posix_only
def test_portal_session_record_is_not_written_through_a_link(data_root, monkeypatch):
    target = outside_file(data_root)
    portal.RUNTIME.mkdir()
    portal.META_FILE.symlink_to(target)
    monkeypatch.setattr(portal, "running", lambda: False)
    monkeypatch.setattr(portal, "prepare_display", lambda name, **kwargs: None)
    monkeypatch.setattr(portal, "notify_party_consumed", lambda *args: None)
    monkeypatch.setattr(portal, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    ok, message = portal.start_session("qwen", "new", "fresh", "a story")
    assert ok, message
    assert target.read_text(encoding="utf-8") == "keep me"
    assert json.loads(portal.META_FILE.read_text(encoding="utf-8"))["campaign"] == "fresh"


@posix_only
def test_arc_pulse_is_not_written_through_a_planted_temporary_link(tmp_path):
    import arc_pulse
    target = outside_file(tmp_path)
    campaign = tmp_path / "campaigns" / "demo"
    campaign.mkdir(parents=True)
    (campaign / "arc_pulse.json.tmp").symlink_to(target)
    (campaign / "arc_pulse.json").symlink_to(target)
    arc_pulse.commit(campaign, arc_pulse.Pulse("beat", 1, 2, 0, None))
    assert target.read_text(encoding="utf-8") == "keep me"
    assert json.loads((campaign / "arc_pulse.json").read_text(encoding="utf-8"))["turns"] == 2


# ── The display, which runs outside the sandbox ───────────────────────────────

@pytest.fixture
def display(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "campaigns" / "demo").mkdir(parents=True)
    (root / "campaigns" / "demo" / "state.md").write_text("# Demo\n\n## Session Flags\n", encoding="utf-8")
    runtime = root / ".runtime"
    runtime.mkdir()
    (runtime / ".campaign").write_text("demo", encoding="utf-8")
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("DND_RUNTIME_DIR", str(runtime))
    monkeypatch.syspath_prepend(str(DISPLAY))
    spec = importlib.util.spec_from_file_location("_write_limits_display", DISPLAY / "dnd-display-app.py")
    app = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "_write_limits_display", app)
    spec.loader.exec_module(app)
    app._audio = None
    app._broadcast = lambda payload: None
    app._music = app._music_module.MusicDirector(runtime / "music", None, None, autostart=False)
    return app, root


@posix_only
def test_display_never_writes_through_links_in_the_runtime_or_campaign(display, tmp_path):
    app, root = display
    target = outside_file(tmp_path)
    runtime, campaign = root / ".runtime", root / "campaigns" / "demo"
    planted = [runtime / name for name in ("text_log.json", "stats.json", "narration_target", "roll_prefs.json",
                                           "client-errors.log", ".input_queue", "player_input.json")]
    planted += [campaign / name for name in ("session_tail.json", "session_tail.json.tmp", "party_stats.json",
                                             "party_stats.json.tmp", "state.md")]
    for link in planted:
        link.unlink(missing_ok=True)
        link.symlink_to(target)
    client = app.app.test_client()
    client.post("/stats", json={"players": [{"name": "Hero"}]})
    client.post("/chunk", json={"text": "The road bends north."})
    client.post("/narration-pref", json={"target_words": 120})
    client.post("/roll-pref", json={"character": "Hero", "mode": "auto"})
    client.post("/clientlog", json={"kind": "error", "message": "echo pwned"})
    app._expected_count = 1
    app._staged["Everybody"] = {"text": "echo pwned", "ready": True}
    app._check_auto_trigger()
    app._persist_input_queue()
    app._write_session_flag(app.re.compile(r"^- roll_mode:.*$", app.re.M), "- roll_mode: auto")
    assert target.read_text(encoding="utf-8") == "keep me"


def test_renderer_settings_come_from_the_data_root_not_the_runtime_dir(display, tmp_path):
    app, root = display
    assert app._music_config_path == root.resolve() / "music-config.json"
    assert app._image_config_path == root.resolve() / "image-config.json"


def test_local_music_setup_saves_where_the_display_reads(tmp_path):
    cli = tmp_path / "sa3"
    cli.write_text("#!/bin/sh\n", encoding="utf-8")
    cli.chmod(0o755)
    root = tmp_path / "data"
    env = dict(os.environ, DND_CAMPAIGN_ROOT=str(root), DND_RUNTIME_DIR=str(root / ".runtime"))
    subprocess.run([sys.executable, str(REPO / "scripts/configure-local-music.py"), "--cli", str(cli),
                    "--hf-home", str(tmp_path)], env=env, check=True, capture_output=True)
    assert json.loads((root / "music-config.json").read_text(encoding="utf-8"))["cli"] == str(cli.resolve())
    assert not (root / ".runtime" / "music-config.json").exists()


def test_help_hint_model_gets_no_tools_and_no_project_settings():
    source = (DISPLAY / "dm_help.py").read_text(encoding="utf-8")
    call = source[source.index('"claude", "-p"'):]
    call = call[:call.index("]")]
    assert '"--tools", ""' in call
    assert '"--setting-sources", ""' in call
    assert '"--strict-mcp-config"' in call


# ── Data-root files the DM can no longer write ───────────────────────────────

@posix_only
def test_name_registry_add_degrades_when_the_registry_is_read_only(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "skills/dnd/scripts"))
    import name_registry
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(root))
    root.chmod(0o500)
    try:
        entry = name_registry.add("Mira Vell", "npc", "demo", 1)
    finally:
        root.chmod(0o700)
    assert entry["name"] == "Mira Vell"
    assert not (root / ".name_registry.json").exists()


# ── Launchers: where the DM starts and which Python they use ──────────────────

def stub_bin(tmp_path, name, body):
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    script = folder / name
    script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script.chmod(0o755)
    return folder


@posix_only
@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_launcher_starts_the_dm_in_the_given_folder(tmp_path, backend):
    folder = stub_bin(tmp_path, backend, 'pwd\nprintf "%s\\n" "$@"\n')
    campaign = tmp_path / "data" / "campaigns" / "demo"
    campaign.mkdir(parents=True)
    env = dict(os.environ, DND_CAMPAIGN_ROOT=str(tmp_path / "data"), DND_VENV=str(tmp_path / "none"),
               PATH=f"{folder}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(["bash", str(REPO / "scripts/play-local.sh"), backend, "--workdir", str(campaign),
                             "--model", "m"], cwd=REPO, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert Path(lines[0]).resolve() == campaign.resolve()
    assert "--workdir" not in lines and lines[-2:] == ["--model", "m"]


def venv_with_python(folder):
    (folder / "bin").mkdir(parents=True)
    (folder / "bin" / "python3").symlink_to(sys.executable)
    return folder


@posix_only
@pytest.mark.parametrize("chosen", ["explicit", "default", "legacy"])
def test_launcher_prefers_a_venv_outside_the_data_root(tmp_path, chosen):
    folder = stub_bin(tmp_path, "codex", 'command -v python3\n')
    home, data = tmp_path / "home", tmp_path / "data"
    venvs = {"explicit": tmp_path / "chosen-venv", "default": home / ".local/share/dnd/venv",
             "legacy": data / ".venv"}
    for name in list(venvs)[list(venvs).index(chosen):]:
        venv_with_python(venvs[name])
    env = dict(os.environ, HOME=str(home), DND_CAMPAIGN_ROOT=str(data), DND_VENV=str(venvs["explicit"]),
               PATH=f"{folder}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(["bash", str(REPO / "scripts/play-local.sh"), "codex"], cwd=REPO, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(venvs[chosen] / "bin" / "python3")


@posix_only
def test_portal_launcher_log_is_not_written_through_a_link(tmp_path):
    target = outside_file(tmp_path)
    data = tmp_path / "data"
    (data / ".runtime").mkdir(parents=True)
    (data / ".runtime" / "portal.log").symlink_to(target)
    state = tmp_path / "curl-state"
    health = json.dumps({"service": "dnd-campaign-portal", "ready": True, "data_root": str(data)})
    folder = stub_bin(tmp_path, "curl", f'[ -e "{state}" ] && {{ printf %s \'{health}\'; exit 0; }}\n'
                                        f'touch "{state}"; exit 1\n')
    venv = venv_with_python(tmp_path / "venv")
    (venv / "bin" / "python3").unlink()
    (venv / "bin" / "python3").write_text('#!/bin/sh\necho "python $*"\n', encoding="utf-8")
    (venv / "bin" / "python3").chmod(0o755)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, DND_CAMPAIGN_ROOT=str(data), DND_VENV=str(venv), DND_PORTAL_BOOT_SERVICES="0",
               DND_PORTAL_PORT=str(port), PATH=f"{folder}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(["bash", str(REPO / "scripts/start-campaign-portal.sh")], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert target.read_text(encoding="utf-8") == "keep me"
    assert "campaign_portal.py" in (data / ".runtime" / "portal.log").read_text(encoding="utf-8")


# ── Found in review: more work done outside the sandbox ──────────────────────

@posix_only
def test_graph_reader_run_by_the_help_button_never_backs_up_through_a_link(tmp_path):
    import datetime
    target = outside_file(tmp_path)
    root = tmp_path / "data"
    campaign = root / "campaigns" / "demo"
    campaign.mkdir(parents=True)
    (campaign / "graph.json").write_text("not json", encoding="utf-8")
    now = datetime.datetime.now()
    for second in range(30):
        stamp = (now + datetime.timedelta(seconds=second)).strftime("%Y%m%d-%H%M%S")
        (campaign / f"graph.json.corrupt-{stamp}").symlink_to(target)
    env = dict(os.environ, DND_CAMPAIGN_ROOT=str(root))
    subprocess.run([sys.executable, str(REPO / "skills/dnd/scripts/campaign_graph.py"), "scene-context",
                    "--campaign", "demo", "--place", "x"], env=env, capture_output=True, timeout=30)
    assert target.read_text(encoding="utf-8") == "keep me"
    assert any(p.is_file() and not p.is_symlink() for p in campaign.glob("graph.json.corrupt-*"))


@pytest.mark.parametrize("text", ["/add-dir /checkout", "!touch x", "  #remember this", "&run", "/permissions"])
def test_portal_reply_can_never_be_a_cli_command(data_root, monkeypatch, text):
    monkeypatch.setattr(portal, "prepare_display", lambda name, **kwargs: None)
    monkeypatch.setattr(portal, "running", lambda: True)
    monkeypatch.setattr(portal, "submit_to_dm", lambda reply: pytest.fail("must not reach the DM"))
    app = portal.create_app()
    client = app.test_client()
    base = "http://127.0.0.1:5002"
    import re
    import urllib.parse
    link = urllib.parse.urlsplit(app.config["NEW_LOGIN_LINK"]())
    page = client.get(link.path, base_url="http://127.0.0.1:5002").get_data(as_text=True)
    client.environ_base["HTTP_X_DND_PORTAL_SESSION"] = json.loads(re.search(r'setItem\("dnd-portal-session", ("[^"]+")\)', page).group(1))
    response = client.post("/api/send", base_url=base, json={"text": text}, headers={"Origin": base})
    assert response.status_code == 400


def test_music_renders_outside_the_runtime_dir_and_publishes_safely(tmp_path):
    import wave
    import music
    music_dir = tmp_path / "runtime" / "music"
    music_dir.mkdir(parents=True)
    job = music.Job(1, "entry", "intro", "prompt", 10, 100, 2.0, music_dir / "entry-intro.wav")
    outs = []

    def render(cmd, **kwargs):
        out = Path(cmd[cmd.index("--out") + 1])
        outs.append(out)
        with wave.open(str(out), "wb") as wav:
            wav.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0" * 1764)

    from unittest import mock
    with mock.patch.object(music.subprocess, "run", side_effect=render):
        music.render_cli(job, Path("/fake/sa3"), None)
    assert music_dir not in outs[0].parents
    assert Path(os.environ["DND_CAMPAIGN_ROOT"]).resolve() in outs[0].parents
    assert job.path.is_file() and not outs[0].parent.exists()


def test_background_renders_outside_the_runtime_dir(tmp_path):
    import backgrounds
    from unittest import mock
    shown = tmp_path / "runtime" / "backgrounds"
    shown.mkdir(parents=True)
    config = backgrounds.LocalConfig(Path(sys.executable), tmp_path / "runner.py", tmp_path)
    job = backgrounds.SceneJob("a" * 32, "cave", "ominous", shown / "image.png")
    used = []

    def launch(command, **kwargs):
        directory = Path(command[command.index("--output") + 1])
        used.append(directory)
        (directory / "source.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + (64).to_bytes(4, "big") * 2 + b"\0" * 32)
        return mock.Mock(wait=mock.Mock(return_value=0))

    with mock.patch.object(backgrounds.subprocess, "Popen", side_effect=launch):
        backgrounds.render_local(job, config)
    assert shown not in used[0].parents and not used[0].exists()
    assert backgrounds.valid_png(job.path)


def test_tls_files_live_in_the_data_root_not_the_runtime_dir():
    setup_tls = load("_write_limits_setup_tls", DISPLAY / "setup_tls.py")
    root = Path(os.environ["DND_CAMPAIGN_ROOT"]).resolve()
    assert Path(setup_tls.CERT_FILE) == root / "tls" / "cert.pem"
    assert Path(setup_tls.KEY_FILE) == root / "tls" / "key.pem"
    script = (DISPLAY / "start-display.sh").read_text(encoding="utf-8")
    assert '--directory "$TLS_DIR/public"' in script and '--directory "$RT"' not in script


def test_display_start_stops_only_its_own_checkouts_display():
    script = (DISPLAY / "start-display.sh").read_text(encoding="utf-8")
    assert 'pkill -9 -f "$DISPLAY_DIR/dnd-display-app\\.py"' in script
    assert 'pkill -9 -f "dnd-display-app\\.py"' not in script
    assert "lsof -nP -t -iTCP:5001 -sTCP:LISTEN" in script and "exit 1" in script
