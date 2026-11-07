"""Exercise the Desktop menu without opening a browser or a model."""

import os
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name == "nt", reason="macOS Bash launcher")


def run_launcher(tmp_path, answers, *args):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "ssh.log"
    scripts = {
        "ssh": "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$DND_TEST_LOG\"\n",
        "curl": "#!/bin/sh\nexit 0\n",
        "uname": "#!/bin/sh\necho Linux\n",
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", DND_TEST_LOG=str(log))
    result = subprocess.run(
        ["bash", str(REPO / "scripts/launch-campaign-desktop.sh"), *args],
        input=answers, capture_output=True, text=True, env=env, timeout=10,
    )
    return result, log.read_text(encoding="utf-8") if log.exists() else ""


def test_new_campaign_uses_qwen_interactive_prompt(tmp_path):
    result, calls = run_launcher(tmp_path, "1\n1\n")
    assert result.returncode == 0, result.stderr
    assert "--start qwen" in calls
    assert "play-local.sh qwen" in calls
    assert "-i" in calls
    assert "--campaign" not in calls
    assert "local\\ browser\\ display" in calls


def test_resume_uses_saved_campaign_and_terra_medium(tmp_path):
    result, calls = run_launcher(tmp_path, "2\n2\nold-campaign\n")
    assert result.returncode == 0, result.stderr
    assert "--list" in calls and "--exists old-campaign" in calls
    assert "play-local.sh codex --campaign old-campaign" in calls
    assert "gpt-6.1-terra" in calls
    assert "model_reasoning_effort" in calls


def test_invalid_campaign_is_rejected_before_service_start(tmp_path):
    result, calls = run_launcher(tmp_path, "1\n2\n../../bad\n")
    assert result.returncode == 2
    assert "--start" not in calls


def test_check_does_not_start_services_or_model(tmp_path):
    result, calls = run_launcher(tmp_path, "1\n1\n", "--check")
    assert result.returncode == 0, result.stderr
    assert "--check qwen" in calls
    assert "--start" not in calls and "play-local.sh" not in calls
