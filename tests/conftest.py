"""Isolate campaign data before pytest imports any application modules."""

import os
import tempfile
import sys
from pathlib import Path


# Fixtures run after collection; application imports can read/write state earlier.
_campaign_sandbox = tempfile.TemporaryDirectory(prefix="dnd-pytest-campaigns-")
os.environ["DND_CAMPAIGN_ROOT"] = _campaign_sandbox.name
os.environ.pop("DND_RUNTIME_DIR", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/dnd/scripts"))
import paths

paths._DEFAULT_ROOT = Path(_campaign_sandbox.name) / "legacy"
