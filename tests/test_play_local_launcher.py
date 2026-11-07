"""Check launcher arguments without starting a model or display."""

import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_qwen_named_campaign_uses_one_system_prompt(tmp_path):
    campaign = tmp_path / "campaigns" / "trial"
    campaign.mkdir(parents=True)
    (campaign / "state.md").write_text("# Trial\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "qwen": "#!/bin/sh\nprintf '%s\\n' \"$@\"\n",
        "curl": "#!/bin/sh\nexit 0\n",
    }.items():
        script = bin_dir / name
        script.write_text(body)
        script.chmod(0o755)
    env = dict(os.environ, DND_CAMPAIGN_ROOT=str(tmp_path), PATH=f"{bin_dir}:{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", "scripts/play-local.sh", "qwen", "--campaign", "trial", "--yolo"],
        cwd=REPO, env=env, capture_output=True, text=True, check=True,
    )
    args = result.stdout.splitlines()
    assert args.count("--append-system-prompt") == 1
    prompt = args[args.index("--append-system-prompt") + 1]
    assert str(campaign) in prompt
    assert args[-1] == "--yolo"
