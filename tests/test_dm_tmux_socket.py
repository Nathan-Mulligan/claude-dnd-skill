"""The DM's tmux session lives on a private socket, and the DM never learns where (#161)."""

import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("campaign_portal_for_tmux", REPO / "scripts/campaign_portal.py")
portal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portal)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="tmux and Unix sockets need a POSIX system")


@pytest.fixture
def private(monkeypatch):
    """A short private folder (socket paths are limited to about 100 bytes) the guard accepts."""
    folder = Path(tempfile.mkdtemp(prefix="t")) / "tmux"
    monkeypatch.setattr(portal, "TMUX_DIR", folder)
    monkeypatch.setattr(portal, "SHARED_TEMP", ())
    monkeypatch.setattr(portal, "_legacy", None)
    monkeypatch.setenv("TMUX", f"{INHERITED[1]},123,0")
    monkeypatch.setenv("TMUX_PANE", "%0")
    yield folder
    shutil.rmtree(folder.parent, ignore_errors=True)


class Servers:
    """Fake tmux servers keyed by how they are named on the command line (-S path or -L name).

    A session is {"id": "$N", "pid": pane pid, "proc": a real process standing in for its DM, if any}."""

    def __init__(self):
        self.sessions = {}
        self.calls = []
        self.next_id = 50

    def add(self, server, name, session_id, proc=None):
        self.sessions.setdefault(server, {})[name] = {"id": session_id, "pid": proc.pid if proc else 99999999,
                                                      "proc": proc}

    def run(self, args, **kwargs):
        self.calls.append((tuple(args), kwargs))
        assert args[0] == "tmux", args
        server, command, rest = tuple(args[1:3]), args[3], list(args[4:])
        sessions = self.sessions.setdefault(server, {})
        target = next((rest[i + 1] for i, value in enumerate(rest) if value in ("-t", "-pt")), "")
        name = target.lstrip("=").split(":")[0]
        found = next((key for key, value in sessions.items() if name in (key, value["id"])), None)
        if command == "new-session":
            self.add(server, rest[rest.index("-s") + 1], f"${self.next_id}")
            self.next_id += 1
            return subprocess.CompletedProcess(args, 0, "", "")
        if not target:
            return subprocess.CompletedProcess(args, 0, "", "")
        if found is None:
            error = f"can't find session: {name}" if sessions else f"no server running on {server[1]}"
            return subprocess.CompletedProcess(args, 1, "", error)
        session = sessions[found]
        if command == "kill-session":
            del sessions[found]
            if session["proc"]:
                session["proc"].kill()
                session["proc"].wait()
        out = ""
        if command == "display-message":
            out = str(session["pid"] if "#{pane_pid}" in rest else session["id"]) + "\n"
        return subprocess.CompletedProcess(args, 0, out, "")

    def servers_used(self, start=0):
        return [call[0][1:3] for call in self.calls[start:]]


def private_server(folder):
    return ("-S", str(folder.resolve() / portal.SESSION))


DEFAULT = ("-L", "default")
INHERITED = ("-S", "/private/tmp/tmux-501/agents")


@pytest.fixture
def saved(tmp_path, monkeypatch):
    """A saved campaign the portal could resume, so a refusal can only come from the running check."""
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(portal, "RUNTIME", tmp_path / ".runtime")
    monkeypatch.setattr(portal, "META_FILE", tmp_path / ".runtime" / "portal-session.json")
    monkeypatch.setattr(portal, "QWEN_ENABLED", True)
    (tmp_path / "campaigns" / "demo").mkdir(parents=True)
    (tmp_path / "campaigns" / "demo" / "state.md").write_text("# Demo\n", encoding="utf-8")
    return tmp_path


def test_every_portal_tmux_call_uses_the_private_socket_without_tmux_variables(private, monkeypatch):
    servers = Servers()
    servers.add(private_server(private), portal.SESSION, "$3")
    monkeypatch.setattr(portal.subprocess, "run", servers.run)
    monkeypatch.setattr(portal.time, "sleep", lambda _: None)
    assert portal.running()
    portal.output()
    assert portal.submit_to_dm("We open the door.")
    identity = portal.tmux_identity()
    assert portal.run("tmux", "kill-session", "-t", identity).returncode == 0
    commands = [call[0][3] for call in servers.calls]
    assert {"has-session", "capture-pane", "load-buffer", "paste-buffer", "send-keys", "display-message",
            "kill-session"} <= set(commands)
    assert set(servers.servers_used()) == {private_server(private)}
    for _, kwargs in servers.calls:
        assert "TMUX" not in kwargs["env"] and "TMUX_PANE" not in kwargs["env"]
    assert private.stat().st_mode & 0o777 == 0o700


def test_party_text_reaches_tmux_through_stdin_not_a_temp_file(private, monkeypatch):
    servers = Servers()
    servers.add(private_server(private), portal.SESSION, "$3")
    monkeypatch.setattr(portal.subprocess, "run", servers.run)
    assert portal.paste_into_dm("[Everybody]: We haggle.")
    load = next((args, kwargs) for args, kwargs in servers.calls if args[3] == "load-buffer")
    assert load[0][-1] == "-" and load[1]["input"] == "[Everybody]: We haggle."


def test_the_dm_starts_on_the_private_socket_and_its_command_drops_tmux_variables(private, saved, monkeypatch):
    monkeypatch.setattr(portal, "prepare_display", lambda name, **kwargs: None)
    monkeypatch.setattr(portal, "notify_party_consumed", lambda *args: None)
    servers = Servers()
    calls = []

    def fake_run(args, **kwargs):
        if args[0] == "tmux":
            return servers.run(args, **kwargs)
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(portal.subprocess, "run", fake_run)
    ok, message = portal.start_session("qwen", "new", "fresh", "a story")
    assert ok, message
    start = next(args for args, _ in servers.calls if args[3] == "new-session")
    assert start[1:3] == private_server(private)
    argv = shlex.split(start[-1])
    assert argv[:5] == ["env", "-u", "TMUX", "-u", "TMUX_PANE"]
    assert set(servers.servers_used()) == {private_server(private)}


def test_tmux_is_only_ever_run_through_the_portals_run():
    source = (REPO / "scripts/campaign_portal.py").read_text(encoding="utf-8")
    # The two in run() itself are where the private socket is added.
    assert len(re.findall(r'(?<!run\()"tmux"', source)) == 2
    assert "os.system" not in source and "Popen" not in source


def test_a_socket_folder_the_dm_could_write_is_refused(private, tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(portal, "TMUX_DIR", tmp_path / ".runtime" / "tmux")
    monkeypatch.setattr(portal.subprocess, "run", lambda *a, **k: pytest.fail("must not reach tmux"))
    assert portal.running() is False
    assert portal.run("tmux", "has-session").returncode == 1
    monkeypatch.setattr(portal, "TMUX_DIR", private)
    monkeypatch.setattr(portal, "SHARED_TEMP", (private.parent,))
    assert portal.running() is False


def test_a_linked_socket_folder_is_refused(private, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    private.parent.mkdir(exist_ok=True)
    private.symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setattr(portal.subprocess, "run", lambda *a, **k: pytest.fail("must not reach tmux"))
    assert portal.running() is False
    with pytest.raises(ValueError):
        portal.tmux_socket()


def test_an_open_socket_folder_is_closed_to_others(private):
    private.mkdir(mode=0o755, parents=True)
    private.chmod(0o755)
    assert portal.tmux_socket() == private.resolve() / portal.SESSION
    assert private.stat().st_mode & 0o777 == 0o700


def test_a_socket_path_too_long_for_unix_is_refused(private, monkeypatch):
    monkeypatch.setattr(portal, "TMUX_DIR", private / ("x" * 120))
    with pytest.raises(ValueError):
        portal.tmux_socket()


# ── The DM that was already running when this update arrived ──────────────────

@pytest.fixture
def old_dm():
    """A real process standing in for the DM that was started before the update."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    if proc.poll() is None:
        proc.kill()
        proc.wait()


def test_an_old_dm_on_the_default_server_is_driven_until_it_stops(private, saved, old_dm, monkeypatch):
    servers = Servers()
    servers.add(DEFAULT, portal.SESSION, "$0", old_dm)
    servers.add(DEFAULT, "another-agent", "$1")
    monkeypatch.setattr(portal.subprocess, "run", servers.run)
    monkeypatch.setattr(portal.time, "sleep", lambda _: None)
    portal.adopt_legacy_session()
    assert portal._legacy == (DEFAULT, old_dm.pid)
    adopted = len(servers.calls)
    assert portal.running()
    assert portal.submit_to_dm("We rest.")
    ok, message = portal.start_session("qwen", "resume", "demo", "")
    assert not ok and "already running" in message
    identity = portal.tmux_identity()
    assert identity == "$0"
    assert set(servers.servers_used(adopted)) == {DEFAULT}
    # What a save handoff does once the DM has saved: stop exactly that session.
    assert portal.run("tmux", "kill-session", "-t", identity).returncode == 0
    assert portal.running() is False
    assert portal._legacy is None
    stopped = len(servers.calls)
    portal.running()
    assert set(servers.servers_used(stopped)) == {private_server(private)}
    assert list(servers.sessions[DEFAULT]) == ["another-agent"]
    kills = [call[0] for call in servers.calls if call[0][3] in ("kill-session", "kill-server")]
    assert kills == [("tmux", *DEFAULT, "kill-session", "-t", "$0")]


def test_an_old_dm_on_the_server_the_portal_was_started_inside_is_found(private, old_dm, monkeypatch):
    servers = Servers()
    servers.add(INHERITED, portal.SESSION, "$2", old_dm)
    monkeypatch.setattr(portal.subprocess, "run", servers.run)
    portal.adopt_legacy_session()
    assert portal._legacy == (INHERITED, old_dm.pid)
    assert portal.running() and set(servers.servers_used(len(servers.calls) - 1)) == {INHERITED}


def test_an_old_dm_whose_socket_was_deleted_still_blocks_a_second_dm(private, saved, old_dm, monkeypatch):
    # The shared socket is in /tmp, so the DM itself could delete it while it keeps running.
    servers = Servers()
    servers.add(DEFAULT, portal.SESSION, "$0", old_dm)
    monkeypatch.setattr(portal.subprocess, "run", servers.run)
    monkeypatch.setattr(portal, "LEGACY_EXIT_WAIT", 0)
    portal.adopt_legacy_session()
    del servers.sessions[DEFAULT]
    assert portal.running() is True
    ok, message = portal.start_session("qwen", "resume", "demo", "")
    assert not ok and "already running" in message
    assert not any(call[0][3] == "new-session" for call in servers.calls)
    old_dm.kill()
    old_dm.wait()
    assert portal.running() is False and portal._legacy is None


def test_no_old_dm_means_each_old_server_is_asked_once(private, monkeypatch):
    servers = Servers()
    servers.add(DEFAULT, "another-agent", "$1")
    monkeypatch.setattr(portal.subprocess, "run", servers.run)
    portal.adopt_legacy_session()
    assert portal._legacy is None
    portal.running()
    probe = ("display-message", "-p", "-t", f"={portal.SESSION}:", "#{pane_pid}")
    old = [call[0] for call in servers.calls if call[0][1:3] != private_server(private)]
    assert old == [("tmux", *INHERITED, *probe), ("tmux", *DEFAULT, *probe)]


def test_a_dm_already_on_the_private_socket_ignores_the_old_servers(private, monkeypatch):
    servers = Servers()
    servers.add(private_server(private), portal.SESSION, "$4")
    servers.add(DEFAULT, portal.SESSION, "$0")
    monkeypatch.setattr(portal.subprocess, "run", servers.run)
    portal.adopt_legacy_session()
    assert portal._legacy is None
    assert set(servers.servers_used()) == {private_server(private)}


def test_a_throwaway_portal_never_adopts_the_live_session(private, monkeypatch):
    monkeypatch.setattr(portal, "SESSION", "tmuxfix-preview")
    servers = Servers()
    servers.add(DEFAULT, "dnd-campaign-portal", "$0")
    monkeypatch.setattr(portal.subprocess, "run", servers.run)
    portal.adopt_legacy_session()
    assert portal._legacy is None
    assert all("dnd-campaign-portal" not in " ".join(call[0]) for call in servers.calls)


# ── A real tmux server ───────────────────────────────────────────────────────

@pytest.mark.skipif(not shutil.which("tmux", path=f"/opt/homebrew/bin:{os.environ.get('PATH', '')}"),
                    reason="tmux is not installed")
def test_a_real_dm_pane_runs_on_the_private_socket_and_cannot_see_it(private, monkeypatch):
    session = "dnd-test-" + uuid.uuid4().hex[:8]
    monkeypatch.setattr(portal, "SESSION", session)
    seen = private.parent / "env.txt"
    command = portal.dm_command("demo", ["sh", "-c", f"env > {shlex.quote(str(seen))}; exec cat"])
    started = portal.run("tmux", "new-session", "-d", "-s", session, "-x", "120", "-y", "20", command)
    assert started.returncode == 0, started.stderr
    try:
        deadline = time.monotonic() + 10
        while not (seen.exists() and seen.read_text(encoding="utf-8")) and time.monotonic() < deadline:
            time.sleep(0.1)
        names = {line.split("=", 1)[0] for line in seen.read_text(encoding="utf-8").splitlines() if "=" in line}
        assert "DND_ACTIVE_CAMPAIGN" in names
        assert not names & {"TMUX", "TMUX_PANE"}
        assert (private / session).exists()
        assert private.stat().st_mode & 0o777 == 0o700
        assert portal.running()
        assert portal.submit_to_dm("hello from the party")
        deadline = time.monotonic() + 5
        while "hello from the party" not in portal.output() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert "hello from the party" in portal.output()
        assert portal.run("tmux", "kill-session", "-t", portal.tmux_identity()).returncode == 0
        assert portal.running() is False
    finally:
        portal.run("tmux", "kill-server")
