"""Keep display narration inside the campaign selected by the launcher."""

import importlib.util
import json
import sys
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
        {"campaign": "old-campaign", "tail_path": "old", "data_root": str(tmp_path)},
        {"campaign": "new-campaign", "tail_path": expected_tail, "data_root": str(tmp_path)},
    ]), mock.patch.object(send, "_post", return_value=True) as post:
        send._ensure_active_campaign()
    assert [call.args[0] for call in post.call_args_list] == [send.CLEAR_URL, send.STATS_URL]
    assert json.loads(post.call_args_list[1].args[1]) == {"campaign": "new-campaign"}


def test_refuses_mismatched_display_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("DND_ACTIVE_CAMPAIGN", "new-campaign")
    with mock.patch.object(send, "_verify_health", return_value={
        "campaign": "new-campaign", "tail_path": "/wrong/root/session_tail.json", "data_root": "/wrong/root",
    }), mock.patch.object(send, "_post") as post:
        with pytest.raises(SystemExit, match="campaign root differs"):
            send._ensure_active_campaign()
    post.assert_not_called()


def test_setup_question_selects_campaign_before_displaying(monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(SOURCE), "--setup-question", "Who rolls the dice?"])
    events = []
    with mock.patch.object(send, "_ensure_active_campaign", side_effect=lambda: events.append("campaign")), \
         mock.patch.object(send, "_read_token", return_value=""), \
         mock.patch.object(send, "_post", side_effect=lambda *args: events.append("question") or True):
        send.main()
    assert events == ["campaign", "question"]


def test_different_campaign_in_wrong_root_is_not_cleared(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("DND_ACTIVE_CAMPAIGN", "fresh")
    with mock.patch.object(send, "_verify_health", return_value={
        "campaign": "previous", "data_root": "/wrong/root", "tail_path": "/wrong/root/previous/session_tail.json",
    }), mock.patch.object(send, "_post") as post:
        with pytest.raises(SystemExit, match="campaign root differs"):
            send._ensure_active_campaign()
    post.assert_not_called()


def test_named_scene_cli_sends_context_only_with_first_narration_chunk(monkeypatch):
    import io
    monkeypatch.setattr(sys, "argv", [str(SOURCE), "--music-event", "scene", "--music-setting", "city",
        "--music-mood", "curious", "--scene-key", "clockwork-room", "--scene-label", "The Clockwork Room",
        "--scene-description", "A room of brass mechanisms."])
    monkeypatch.setattr(sys, "stdin", io.StringIO("The door opens.\n\nYou step inside."))
    monkeypatch.setattr(send, "_ensure_active_campaign", lambda: None)
    monkeypatch.setattr(send, "_read_token", lambda: "")
    monkeypatch.setattr(send, "_split_paragraphs", lambda text: text.split("\n\n"))
    payloads = []
    monkeypatch.setattr(send, "_post", lambda url, data, token: payloads.append(json.loads(data)) or True)
    send.main()
    assert payloads[0]["music"]["scene"] == {"key": "clockwork-room", "label": "The Clockwork Room", "description": "A room of brass mechanisms."}
    assert "music" not in payloads[1]


@pytest.mark.parametrize("args", [
    ["--scene-key", "clockwork-room"],
    ["--music-event", "combat-start", "--scene-key", "room"],
    ["--music-event", "scene", "--music-setting", "city", "--scene-key", "partial"],
])
def test_invalid_scene_cli_flags_fail_before_campaign_operations(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", [str(SOURCE), *args])
    with mock.patch.object(send, "_ensure_active_campaign") as ensure:
        with pytest.raises(SystemExit):
            send.main()
        ensure.assert_not_called()


def test_cli_theme_preserves_omitted_mood_for_inheritance(monkeypatch):
    import io
    theme = [{'name': 'Greenwood', 'mood': 'warm'}, {'name': 'Hut'}]
    monkeypatch.setattr(sys, 'argv', [str(SOURCE), '--music-event', 'scene', '--music-setting', 'tavern',
        '--scene-key', 'hut', '--scene-label', 'The Hut', '--scene-description', 'A timber cottage.', '--scene-theme', json.dumps(theme)])
    monkeypatch.setattr(sys, 'stdin', io.StringIO('The door opens.'))
    monkeypatch.setattr(send, '_ensure_active_campaign', lambda: None)
    monkeypatch.setattr(send, '_read_token', lambda: '')
    payloads = []
    monkeypatch.setattr(send, '_post', lambda url, data, token: payloads.append(json.loads(data)) or True)
    send.main()
    assert 'mood' not in payloads[0]['music']
    assert payloads[0]['music']['scene']['theme'] == theme


@pytest.mark.parametrize('value', ['null', '{"name":"Forest"}', '[{}]', '[{"name":"Forest","mood":"wrong"}]'])
def test_invalid_cli_theme_fails_before_campaign_operations(monkeypatch, value):
    monkeypatch.setattr(sys, 'argv', [str(SOURCE), '--music-event', 'scene', '--music-setting', 'forest',
        '--scene-key', 'f', '--scene-label', 'Forest', '--scene-description', 'Trees.', '--scene-theme', value])
    with mock.patch.object(send, '_ensure_active_campaign') as ensure:
        with pytest.raises(SystemExit):
            send.main()
        ensure.assert_not_called()
