"""Isolate campaign data before pytest imports any application modules."""

import os
import tempfile
import sys
from pathlib import Path

import pytest


# Fixtures run after collection; application imports can read/write state earlier.
_campaign_sandbox = tempfile.TemporaryDirectory(prefix="dnd-pytest-campaigns-")
os.environ["DND_CAMPAIGN_ROOT"] = _campaign_sandbox.name
os.environ.pop("DND_RUNTIME_DIR", None)
# The sidebar-notes helper calls a real model; never start it from tests.
os.environ["DND_LORE_SYNC"] = "0"
# Music and background renderers are configured by these or by runtime JSON files;
# without either, the display never starts a real render during tests.
for _name in [name for name in os.environ if name.startswith(("DND_MUSIC_", "DND_IMAGE_"))]:
    os.environ.pop(_name)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/dnd/scripts"))
import paths

paths._DEFAULT_ROOT = Path(_campaign_sandbox.name) / "legacy"


@pytest.fixture(autouse=True)
def _no_real_dm_tmux_server(request, monkeypatch):
    """Keep every test away from the real DM's tmux socket. In the temp folder the portal refuses to run
    tmux at all, so only a test that clears portal.SHARED_TEMP itself can reach a (private) server."""
    portal = getattr(request.module, "portal", None)
    if portal is not None and hasattr(portal, "TMUX_DIR"):
        monkeypatch.setattr(portal, "TMUX_DIR", Path(_campaign_sandbox.name) / "tmux")
