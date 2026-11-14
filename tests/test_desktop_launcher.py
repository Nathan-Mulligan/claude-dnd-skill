"""Exercise the Desktop menu without opening a browser or a model."""

import os
import subprocess
import time
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name == "nt", reason="macOS Bash launcher")


def run_launcher(tmp_path, answers, *args, curl_response=None, port_occupied=False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "ssh.log"
    scripts = {
        "ssh": "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$DND_TEST_LOG\"\n",
        "curl": "#!/bin/sh\nprintf '%s' \"$DND_TEST_HEALTH\"\n",
        "lsof": f"#!/bin/sh\nexit {0 if port_occupied else 1}\n",
        "uname": "#!/bin/sh\necho Linux\n",
    }
    for name, body in scripts.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    health = curl_response or '{"alive":true,"campaign":"","tail_buffer":0,"tail_path":"","data_root":"/tmp"}'
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               DND_TEST_LOG=str(log), DND_TEST_HEALTH=health)
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


def test_random_ping_listener_is_rejected(tmp_path):
    result, calls = run_launcher(tmp_path, "1\n1\n", curl_response="ok", port_occupied=True)
    assert result.returncode == 1
    assert "port 5001 is used by another service" in result.stderr
    assert "play-local.sh" not in calls


def run_m5_helper(tmp_path, mode, health_root, *, fresh=False, kokoro_up=True, speech_ok=True, docker_ok=True,
                  speech_delay=0):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "services.log"
    for name, body in {
        "curl": (f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$DND_TEST_CURL_LOG\"\n"
                 f"case \"$*\" in *'/health') printf '{{\"alive\":true,\"campaign\":\"{' ' if fresh else 'demo'}\",\"tail_buffer\":0,\"data_root\":\"{health_root}\",\"tail_path\":\"{'' if fresh else str(health_root) + '/campaigns/demo/session_tail.json'}\"}}' ;;\n"
                 f"  *'/v1/audio/voices') exit {0 if kokoro_up else 7} ;;\n"
                 f"  *'/v1/audio/speech') sleep {speech_delay}; exit {0 if speech_ok else 28} ;; esac\n"),
        "docker": f"#!/bin/sh\necho docker \"$*\" >> \"$DND_TEST_LOG\"\nexit {0 if docker_ok else 1}\n",
        "ollama": "#!/bin/sh\necho ollama >> \"$DND_TEST_LOG\"\nprintf 'qwen3.6:35b-mlx\\n'\n",
        "qwen": "#!/bin/sh\necho qwen >> \"$DND_TEST_LOG\"\n",
    }.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               DND_TEST_LOG=str(log), DND_TEST_CURL_LOG=str(tmp_path / "curl.log"),
               DND_CAMPAIGN_ROOT=str(tmp_path / "data"))
    args = [mode] if mode == "--stop-voice" else [mode, "qwen"]
    result = subprocess.run(
        ["bash", str(REPO / "scripts/launch-campaign-m5.sh"), *args],
        capture_output=True, text=True, env=env, timeout=10,
    )
    return result, log.read_text(encoding="utf-8") if log.exists() else ""


def curl_calls(tmp_path, wait_for=None):
    log = tmp_path / "curl.log"
    deadline = time.monotonic() + 5
    while True:
        calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        if wait_for is None or any(wait_for in call for call in calls) or time.monotonic() > deadline:
            return calls
        time.sleep(0.05)


def test_m5_check_leaves_healthy_services_alone(tmp_path):
    result, calls = run_m5_helper(tmp_path, "--check", tmp_path / "data")
    assert result.returncode == 0, result.stderr
    assert "Display: ready" in result.stdout and "Kokoro: ready" in result.stdout
    assert calls == ""


def test_m5_start_reuses_healthy_services(tmp_path):
    result, calls = run_m5_helper(tmp_path, "--start", tmp_path / "data")
    assert result.returncode == 0, result.stderr
    assert "Display: ready" in result.stdout
    assert calls == "ollama\n"


def test_m5_start_accepts_fresh_display_without_campaign(tmp_path):
    result, calls = run_m5_helper(tmp_path, "--start", tmp_path / "data", fresh=True)
    assert result.returncode == 0, result.stderr
    assert "Display: ready" in result.stdout
    assert calls == "ollama\n"


def test_m5_start_rejects_other_campaign_root(tmp_path):
    result, calls = run_m5_helper(tmp_path, "--start", tmp_path / "other", fresh=True)
    assert result.returncode == 1
    assert "another campaign data root" in result.stderr
    assert calls == ""


def test_m5_start_warms_kokoro_with_one_short_line(tmp_path):
    result, _ = run_m5_helper(tmp_path, "--start", tmp_path / "data")
    assert result.returncode == 0, result.stderr
    calls = curl_calls(tmp_path, wait_for="/v1/audio/speech")
    [warm] = [call for call in calls if "/v1/audio/speech" in call]
    assert "Ready." in warm and "--max-time" in warm
    assert calls.index(warm) > max(i for i, call in enumerate(calls) if "/v1/audio/voices" in call)


def test_m5_start_carries_on_when_the_warm_up_fails(tmp_path):
    result, _ = run_m5_helper(tmp_path, "--start", tmp_path / "data", speech_ok=False)
    assert result.returncode == 0, result.stderr
    assert "Kokoro: ready" in result.stdout


def test_m5_start_does_not_wait_for_the_warm_up(tmp_path):
    began = time.monotonic()
    result, _ = run_m5_helper(tmp_path, "--start", tmp_path / "data", speech_delay=4)
    assert result.returncode == 0, result.stderr
    assert time.monotonic() - began < 3


def test_m5_check_does_not_warm_kokoro(tmp_path):
    result, _ = run_m5_helper(tmp_path, "--check", tmp_path / "data")
    assert result.returncode == 0, result.stderr
    assert not any("/v1/audio/speech" in call for call in curl_calls(tmp_path))


def test_m5_stop_voice_stops_the_kokoro_container(tmp_path):
    result, calls = run_m5_helper(tmp_path, "--stop-voice", tmp_path / "data", kokoro_up=False)
    assert result.returncode == 0, result.stderr
    assert calls == "docker stop kokoro-tts\n"


def test_m5_stop_voice_is_harmless_when_kokoro_is_already_down(tmp_path):
    result, _ = run_m5_helper(tmp_path, "--stop-voice", tmp_path / "data", kokoro_up=False, docker_ok=False)
    assert result.returncode == 0, result.stderr


def test_m5_stop_voice_reports_a_kokoro_that_keeps_running(tmp_path):
    result, _ = run_m5_helper(tmp_path, "--stop-voice", tmp_path / "data", kokoro_up=True, docker_ok=False)
    assert result.returncode == 1
    assert "Kokoro" in result.stderr
