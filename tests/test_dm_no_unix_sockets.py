"""A DM's shell can reach the display over localhost TCP but no Unix socket, so no tmux server (#171)."""

import http.server
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


portal = load("_no_unix_sockets_portal", REPO / "scripts/campaign_portal.py")


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(portal, "RUNTIME", tmp_path / ".runtime")
    return tmp_path


def codex_overrides(args):
    """Each `-c key=value` the portal passes Codex, keyed by its dotted path, the value as written."""
    return dict(args[i + 1].partition("=")[::2] for i, value in enumerate(args[:-1]) if value == "-c")


@pytest.mark.parametrize("provider", ["codex", "codex-sol", "codex-astra", "codex-luna"])
def test_codex_dm_network_goes_through_a_proxy_that_allows_no_unix_socket(data_root, provider):
    args = portal.model_args(provider, "resume", "demo", "")
    overrides = codex_overrides(args)
    # Plain `network_access=true` lets every command connect to any Unix socket.
    assert "-s" not in args and "--sandbox" not in args
    assert not any(key.startswith("sandbox_workspace_write") for key in overrides)
    profile = json.loads(overrides["default_permissions"])
    prefix = f"permissions.{profile}."
    assert overrides[prefix + "extends"] == '":workspace"'
    assert overrides[prefix + "network.enabled"] == "true"
    # "limited" lets only GET through, and send.py POSTs.
    assert overrides[prefix + "network.mode"] == '"full"'
    assert overrides[prefix + "network.dangerously_allow_all_unix_sockets"] == "false"
    assert not any(key.startswith(prefix + "network.unix_sockets") for key in overrides)
    domains = json.loads(overrides[prefix + "network.domains"].replace('"="', '":"'))
    assert domains["localhost"] == "allow" and domains["127.0.0.1"] == "allow"
    assert args[args.index("-a") + 1] == "never"


def test_each_codex_launch_gets_a_profile_name_no_config_file_defines(data_root):
    # A `[permissions.<name>]` table in a config file would merge into a fixed name's grants.
    names = {json.loads(codex_overrides(portal.model_args("codex", "resume", "demo", ""))["default_permissions"])
             for _ in range(3)}
    assert len(names) == 3 and all(name.startswith("dnd_dm_") for name in names)


def test_codex_proxy_feature_settings_are_pinned_over_any_config_file(data_root):
    # Codex applies the feature's settings after the profile's, so a config file's could reopen sockets.
    overrides = codex_overrides(portal.model_args("codex", "resume", "demo", ""))
    assert overrides["features.network_proxy.enabled"] == "true"
    assert overrides["features.network_proxy.dangerously_allow_all_unix_sockets"] == "false"
    assert overrides["features.network_proxy.mode"] == '"full"'


# ── Config files a launch flag can't override ─────────────────────────────────

needs_tomllib = pytest.mark.skipif(sys.version_info < (3, 11), reason="the portal reads Codex config with tomllib")


@pytest.fixture
def codex_files(tmp_path, monkeypatch):
    config = tmp_path / "codex" / "config.toml"
    config.parent.mkdir()
    monkeypatch.setattr(portal, "CODEX_CONFIG", config)
    monkeypatch.setattr(portal, "CODEX_MANAGED", (tmp_path / "requirements.toml",))
    monkeypatch.setattr(portal, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""))
    return config


@needs_tomllib
@pytest.mark.parametrize("text", [
    '[features.network_proxy.unix_sockets]\n"/private/tmp" = "allow"\n',
    '[features.network_proxy]\nallow_unix_sockets = ["/private/tmp"]\n',
    '[features\n',
])
def test_codex_dm_is_refused_when_its_config_file_opens_unix_sockets(codex_files, text):
    # A launch flag can't take an allowed socket out of a config file's list; a file Codex can't read
    # is refused too, as it can't be checked.
    codex_files.write_text(text, encoding="utf-8")
    assert "Unix socket" in portal.provider_error("codex")
    assert portal.provider_error("claude") is None


@needs_tomllib
@pytest.mark.parametrize("text", [None, "[features]\nhooks = true\n", "[features]\nnetwork_proxy = true\n",
                                  '[features.network_proxy.unix_sockets]\n"/private/tmp" = "deny"\n'])
def test_codex_dm_starts_when_no_config_file_opens_unix_sockets(codex_files, text):
    if text is not None:
        codex_files.write_text(text, encoding="utf-8")
    assert portal.provider_error("codex-sol") is None


def test_codex_dm_is_refused_under_a_managed_codex_policy(codex_files):
    # Managed requirements outrank every launch flag, so the portal can't promise the DM's sandbox.
    portal.CODEX_MANAGED[0].write_text("", encoding="utf-8")
    assert "managed" in portal.provider_error("codex")


@pytest.fixture
def claude_files(tmp_path, monkeypatch):
    user, managed = tmp_path / "settings.json", tmp_path / "managed-settings.json"
    monkeypatch.setattr(portal, "CLAUDE_SETTINGS", (user, managed))
    monkeypatch.setattr(portal, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""))
    return user, managed


@pytest.mark.parametrize("which", [0, 1])
@pytest.mark.parametrize("text", [
    json.dumps({"sandbox": {"network": {"allowUnixSockets": ["/private/tmp"]}}}),
    json.dumps({"sandbox": {"network": {"allowAllUnixSockets": True}}}),
    json.dumps({"sandbox": {"excludedCommands": ["tmux *"]}}),
    "{not json",
])
def test_claude_dm_is_refused_when_its_settings_open_unix_sockets_or_skip_the_sandbox(claude_files, which, text):
    # Lists merge across settings files, so --settings can't empty them.
    claude_files[which].write_text(text, encoding="utf-8")
    assert "Unix socket" in portal.provider_error("claude-fable")
    assert portal.provider_error("codex") is None


@pytest.mark.parametrize("text", [None, json.dumps({"theme": "dark", "hooks": {}}),
                                  json.dumps({"sandbox": {"network": {"allowUnixSockets": []}}})])
def test_claude_dm_starts_when_its_settings_keep_unix_sockets_closed(claude_files, text):
    if text is not None:
        claude_files[0].write_text(text, encoding="utf-8")
    assert portal.provider_error("claude") is None


@pytest.mark.parametrize("provider", ["claude", "claude-sonnet", "claude-fable"])
def test_claude_dm_sandbox_allows_no_unix_socket(data_root, provider):
    args = portal.model_args(provider, "resume", "demo", "")
    network = json.loads(args[args.index("--settings") + 1])["sandbox"]["network"]
    # --settings outranks the user's settings, so a `true` there can't reopen every socket.
    assert network["allowAllUnixSockets"] is False
    assert "allowUnixSockets" not in network
    assert network["allowLocalBinding"] is True


# ── The real Codex sandbox, where a recent Codex is installed (the M5) ────────

def codex_bin():
    for candidate in (os.environ.get("DND_TEST_CODEX"), str(Path.home() / ".local/bin/codex")):
        if candidate and os.access(candidate, os.X_OK):
            probe = subprocess.run([candidate, "sandbox", "--help"], capture_output=True, text=True)
            if "--permission-profile" in probe.stdout:
                return candidate
    return None


class _Ok(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


PROBE = """
import socket, sys, urllib.request
try:
    s = socket.socket(socket.AF_UNIX)
    s.connect(sys.argv[1])
    print("unix: connected")
except OSError as error:
    print("unix: refused", error.errno)
request = urllib.request.Request(f"http://localhost:{sys.argv[2]}/chunk", data=b"{}", method="POST")
print("display:", urllib.request.urlopen(request, timeout=10).status)
"""


# What a config file could say that the launch flags must override.
HOSTILE_CODEX_CONFIG = """
[features.network_proxy]
enabled = true
dangerously_allow_all_unix_sockets = true
mode = "limited"
"""


@pytest.mark.skipif(sys.platform != "darwin" or codex_bin() is None,
                    reason="needs macOS and a Codex with permission profiles")
@pytest.mark.parametrize("config", ["owner", "hostile"])
def test_codex_sandbox_refuses_a_unix_socket_and_still_posts_to_localhost(data_root, config):
    folder = Path(tempfile.mkdtemp(prefix="dm-sock-", dir="/tmp"))
    env = dict(os.environ)
    if config == "hostile":
        # The sandbox needs no sign-in, so a throwaway CODEX_HOME stands in for a config file.
        (folder / "home").mkdir()
        (folder / "home" / "config.toml").write_text(HOSTILE_CODEX_CONFIG, encoding="utf-8")
        env["CODEX_HOME"] = str(folder / "home")
    listener = socket.socket(socket.AF_UNIX)
    server = http.server.HTTPServer(("127.0.0.1", 0), _Ok)
    try:
        listener.bind(str(folder / "s"))
        listener.listen(1)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        (folder / "probe.py").write_text(PROBE, encoding="utf-8")
        args = portal.model_args("codex", "resume", "demo", "")
        flags = [part for i, flag in enumerate(args[:-1]) if flag == "-c" for part in args[i:i + 2]]
        profile = json.loads(codex_overrides(args)["default_permissions"])
        done = subprocess.run(
            [codex_bin(), "sandbox", "-P", profile, "-C", str(folder), *flags, "--",
             sys.executable, "-I", str(folder / "probe.py"), str(folder / "s"), str(server.server_port)],
            capture_output=True, text=True, timeout=60, env=env)
        assert "unix: refused" in done.stdout, done.stdout + done.stderr
        assert "display: 200" in done.stdout, done.stdout + done.stderr
    finally:
        server.shutdown()
        listener.close()
        shutil.rmtree(folder, ignore_errors=True)
