"""Reusing the path module must never send a new app's writes to an old root."""

import hashlib
import importlib.util
import sys
import io
import json
import unittest
from unittest import mock
from pathlib import Path


DISPLAY = Path(__file__).resolve().parents[1] / "skills/dnd/display"


def load_module(name, path, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_reused_runtime_module_isolates_new_app(tmp_path, monkeypatch):
    roots = [tmp_path / name for name in ("original", "new")]
    for root in roots:
        runtime = root / ".runtime"
        runtime.mkdir(parents=True)
        campaign = root / "campaigns" / "sentinel"
        campaign.mkdir(parents=True)
        (runtime / ".campaign").write_text("sentinel", encoding="utf-8")
        (runtime / "text_log.json").write_text("[]", encoding="utf-8")
        (runtime / "stats.json").write_text('{}', encoding="utf-8")
        (campaign / "session_tail.json").write_text("[]", encoding="utf-8")
    original, new = roots
    protected = [original / ".runtime" / name for name in ("text_log.json", "stats.json")]
    protected.append(original / "campaigns/sentinel/session_tail.json")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    monkeypatch.syspath_prepend(str(DISPLAY))
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(original))
    monkeypatch.setenv("DND_RUNTIME_DIR", str(original / ".runtime"))
    paths = load_module("runtime_paths", DISPLAY / "runtime_paths.py", monkeypatch)
    assert paths.rt("stats.json") == str(protected[1])
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(new))
    monkeypatch.setenv("DND_RUNTIME_DIR", str(new / ".runtime"))
    app = load_module("_runtime_isolation_display", DISPLAY / "dnd-display-app.py", monkeypatch)
    app._audio = None
    app._music = app._music_module.MusicDirector(new / "music", None, None, autostart=False)
    client = app.app.test_client()
    assert client.post("/chunk", json={"text": "Isolated test narration."}).status_code == 204
    assert client.post("/stats", json={"players": [{"name": "Sandbox Hero"}]}).status_code == 204
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    assert "Isolated test narration." in (new / ".runtime/text_log.json").read_text(encoding="utf-8")
    assert "Isolated test narration." in (new / "campaigns/sentinel/session_tail.json").read_text(encoding="utf-8")
    assert "Sandbox Hero" in (new / ".runtime/stats.json").read_text(encoding="utf-8")


def test_campaign_root_change_and_explicit_rt_override(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(DISPLAY))
    monkeypatch.delenv("DND_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path / "first"))
    paths = load_module("runtime_paths", DISPLAY / "runtime_paths.py", monkeypatch)
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path / "second"))
    assert paths.rt("state") == str(tmp_path / "second/.runtime/state")
    monkeypatch.setattr(paths, "RT", str(tmp_path / "explicit"))
    assert paths.rt("state") == str(tmp_path / "explicit/state")


def test_badge_browser_test_refuses_a_different_display_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path / "sandbox"))
    badge = load_module("_isolated_badge_guard", Path(__file__).with_name("test_badge_render.py"), monkeypatch)
    health = io.BytesIO(json.dumps({"data_root": str(tmp_path / "other")}).encode("utf-8"))
    with mock.patch.object(badge.urllib.request, "urlopen", return_value=health), mock.patch.object(badge.subprocess, "run") as run:
        with unittest.TestCase().assertRaises(unittest.SkipTest):
            badge.BadgeRenderTests.setUpClass()
        run.assert_not_called()
