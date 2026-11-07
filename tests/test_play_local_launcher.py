"""Check launcher arguments without starting a model or display."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="Bash launcher integration needs a POSIX shell"
)


def test_qwen_named_campaign_uses_one_system_prompt(tmp_path):
    campaign = tmp_path / "campaigns" / "trial"
    campaign.mkdir(parents=True)
    (campaign / "state.md").write_text("# Trial\n", encoding="utf-8")
    local_bin = tmp_path / ".venv" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "python3").symlink_to(sys.executable)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "qwen": "#!/bin/sh\nprintf '%s\\n' \"$@\"\nprintf 'PYTHON_PATH=%s\\n' \"$(command -v python3)\"\n",
        "curl": "#!/bin/sh\nexit 0\n",
    }.items():
        script = bin_dir / name
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)
    env = dict(os.environ, DND_CAMPAIGN_ROOT=str(tmp_path), PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", "scripts/play-local.sh", "qwen", "--campaign", "trial", "--yolo"],
        cwd=REPO, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    args = result.stdout.splitlines()
    assert args.count("--append-system-prompt") == 1
    prompt = args[args.index("--append-system-prompt") + 1]
    assert str(campaign) in prompt
    assert args[-2] == "--yolo"
    assert args[-1] == f"PYTHON_PATH={local_bin / 'python3'}"


def test_claude_loads_this_checkout_plugin(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", "scripts/play-local.sh", "claude", "--model", "sonnet"],
        cwd=REPO, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["--plugin-dir", str(REPO), "--model", "sonnet"]


def test_codex_named_campaign_uses_shared_root_and_forwards_model(tmp_path):
    campaign = tmp_path / "campaigns" / "trial"
    campaign.mkdir(parents=True)
    (campaign / "state.md").write_text("# Trial\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "codex"
    stub.write_text(
        "#!/bin/sh\nprintf 'ROOT=%s\\nACTIVE=%s\\n' "
        "\"$DND_CAMPAIGN_ROOT\" \"$DND_ACTIVE_CAMPAIGN\"\nprintf '%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    env = dict(os.environ, DND_CAMPAIGN_ROOT=str(tmp_path),
               PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", "scripts/play-local.sh", "codex", "--campaign", "trial",
         "--model", "gpt-6.1-terra"],
        cwd=REPO, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"ROOT={tmp_path}", "ACTIVE=trial", "--model", "gpt-6.1-terra",
    ]
