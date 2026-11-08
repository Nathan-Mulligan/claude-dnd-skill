"""Local image configuration survives display launches and names do not trigger effects."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock


DISPLAY = Path(__file__).resolve().parents[1] / "skills/dnd/display"
sys.path.insert(0, str(DISPLAY))
import audio
import backgrounds


def test_local_renderer_configuration_survives_without_launch_environment(tmp_path):
    runner = tmp_path / "runner.py"
    runner.write_text("", encoding="utf-8")
    runtime = tmp_path / "runtime"
    env = {name: value for name, value in os.environ.items() if not name.startswith("DND_IMAGE_")}
    env["DND_RUNTIME_DIR"] = str(runtime)
    command = [sys.executable, str(DISPLAY / "backgrounds.py"), "--python", sys.executable,
               "--runner", str(runner), "--model", str(tmp_path)]
    subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    target = runtime / "image-config.json"
    saved = target.read_bytes()
    with mock.patch.dict(os.environ, {}, clear=True):
        config = backgrounds.runtime_config(target)
    assert config is not None
    assert config.runner == runner.resolve()
    command[command.index("--runner") + 1] = str(tmp_path / "missing.py")
    assert subprocess.run(command, env=env, capture_output=True).returncode != 0
    assert target.read_bytes() == saved
    assert json.loads(saved)["DND_IMAGE_PYTHON"] == sys.executable


def test_character_name_does_not_make_impact_sound(monkeypatch):
    events = []
    monkeypatch.setattr(audio, "_sfx_on", True)
    monkeypatch.setattr(audio, "_broadcast_fn", events.append)
    audio.on_text("Karate Kick greets the friendly soldier.", ignored_names=["Karate Kick"])
    assert events == []
    audio.on_text("Karate Kick kicks the loose stone.", ignored_names=["Karate Kick"])
    assert events == [{"sfx": "impact"}]
