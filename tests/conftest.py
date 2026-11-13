"""Isolate campaign data before pytest imports any application modules."""

import os
import tempfile


# Fixtures run after collection; application imports can read/write state earlier.
_campaign_sandbox = tempfile.TemporaryDirectory(prefix="dnd-pytest-campaigns-")
os.environ["DND_CAMPAIGN_ROOT"] = _campaign_sandbox.name
# Derive runtime state from the sandbox root; tests may override either variable.
os.environ.pop("DND_RUNTIME_DIR", None)
