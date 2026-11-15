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
    assert args[args.index("--enable") + 1] == "network_proxy"
    assert args[args.index("-a") + 1] == "never"


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


@pytest.mark.skipif(sys.platform != "darwin" or codex_bin() is None,
                    reason="needs macOS and a Codex with permission profiles")
def test_codex_sandbox_refuses_a_unix_socket_and_still_posts_to_localhost(data_root):
    folder = Path(tempfile.mkdtemp(prefix="dm-sock-", dir="/tmp"))
    listener = socket.socket(socket.AF_UNIX)
    server = http.server.HTTPServer(("127.0.0.1", 0), _Ok)
    try:
        listener.bind(str(folder / "s"))
        listener.listen(1)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        (folder / "probe.py").write_text(PROBE, encoding="utf-8")
        args = portal.model_args("codex", "resume", "demo", "")
        flags = [part for i, flag in enumerate(args[:-1]) if flag in ("-c", "--enable") for part in args[i:i + 2]]
        profile = json.loads(codex_overrides(args)["default_permissions"])
        done = subprocess.run(
            [codex_bin(), "sandbox", "-P", profile, "-C", str(folder), *flags, "--",
             sys.executable, "-I", str(folder / "probe.py"), str(folder / "s"), str(server.server_port)],
            capture_output=True, text=True, timeout=60)
        assert "unix: refused" in done.stdout, done.stdout + done.stderr
        assert "display: 200" in done.stdout, done.stdout + done.stderr
    finally:
        server.shutdown()
        listener.close()
        shutil.rmtree(folder, ignore_errors=True)
