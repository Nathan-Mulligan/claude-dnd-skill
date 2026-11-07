import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATHS = ROOT / "skills" / "dnd" / "scripts" / "paths.py"


def test_campaign_path_uses_configured_root_without_creating_files(tmp_path):
    env = os.environ.copy()
    env["DND_CAMPAIGN_ROOT"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(PATHS), "campaign-path", "mock-campaign"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    expected = tmp_path / "campaigns" / "mock-campaign"
    assert result.stdout.strip() == str(expected)
    assert not expected.exists()
