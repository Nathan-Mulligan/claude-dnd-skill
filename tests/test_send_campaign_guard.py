"""Keep display narration inside the campaign selected by the launcher."""

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "skills/dnd/display/send.py"
SPEC = importlib.util.spec_from_file_location("dnd_send_guard", SOURCE)
send = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(send)


def test_switches_display_before_narration(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("DND_ACTIVE_CAMPAIGN", "new-campaign")
    expected_tail = str(tmp_path / "campaigns/new-campaign/session_tail.json")
    with mock.patch.object(send, "_verify_health", side_effect=[
        {"campaign": "old-campaign", "tail_path": "old"},
        {"campaign": "new-campaign", "tail_path": expected_tail},
    ]), mock.patch.object(send, "_post", return_value=True) as post:
        send._ensure_active_campaign()
    assert [call.args[0] for call in post.call_args_list] == [send.CLEAR_URL, send.STATS_URL]
    assert json.loads(post.call_args_list[1].args[1]) == {"campaign": "new-campaign"}


def test_refuses_mismatched_display_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("DND_ACTIVE_CAMPAIGN", "new-campaign")
    with mock.patch.object(send, "_verify_health", return_value={
        "campaign": "new-campaign", "tail_path": "/wrong/root/session_tail.json",
    }), mock.patch.object(send, "_post") as post:
        with pytest.raises(SystemExit, match="campaign root differs"):
            send._ensure_active_campaign()
    post.assert_not_called()
