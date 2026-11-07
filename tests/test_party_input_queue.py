"""Party answers remain queued until the active DM consumes them."""

import importlib.util
import io
import os
import sys
from pathlib import Path
from unittest import mock


DISPLAY = Path(__file__).resolve().parents[1] / "skills/dnd/display"


def test_two_setup_answers_are_not_overwritten(tmp_path):
    spec = importlib.util.spec_from_file_location("_party_input_app_test", DISPLAY / "dnd-display-app.py")
    assert spec and spec.loader
    with mock.patch.dict(os.environ, {"DND_RUNTIME_DIR": str(tmp_path)}):
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
    app.QUEUE_FILE = str(tmp_path / ".input_queue")
    app._expected_count = 1
    app._broadcast = lambda payload: None

    app._staged["Everybody"] = {"text": "My name is Enther", "ready": True}
    app._check_auto_trigger()
    app._staged["Everybody"] = {"text": "I want a grim adventure", "ready": True}
    app._check_auto_trigger()

    assert Path(app.QUEUE_FILE).read_text(encoding="utf-8") == (
        "[Everybody]: My name is Enther\n[Everybody]: I want a grim adventure"
    )


def test_setup_question_reaches_display_without_story_log(tmp_path):
    spec = importlib.util.spec_from_file_location("_setup_question_app_test", DISPLAY / "dnd-display-app.py")
    assert spec and spec.loader
    with mock.patch.dict(os.environ, {"DND_RUNTIME_DIR": str(tmp_path)}):
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
    events = []
    app._broadcast = events.append
    before = len(app._tail_buffer)

    response = app.app.test_client().post("/setup-question", json={"text": "What is your character's name?"})

    assert response.status_code == 204
    assert events[-1] == {"setup_question": "What is your character's name?"}
    assert len(app._tail_buffer) == before


def test_setup_question_sender_works_before_campaign_selection(tmp_path):
    spec = importlib.util.spec_from_file_location("_setup_sender_test", DISPLAY / "send.py")
    assert spec and spec.loader
    with mock.patch.dict(os.environ, {"DND_RUNTIME_DIR": str(tmp_path)}):
        sender = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sender)
    sent = []
    with mock.patch.object(sys, "argv", ["send.py", "--setup-question"]), \
         mock.patch.object(sys, "stdin", io.StringIO("What is your character's name?")), \
         mock.patch.object(sender, "_post", side_effect=lambda *args: sent.append(args) or True), \
         mock.patch.object(sender, "_ensure_active_campaign", side_effect=AssertionError("not selected")):
        sender.main()
    assert sent[0][0] == sender.SETUP_URL
    assert b"What is your character's name?" in sent[0][1]


def test_setup_question_sender_accepts_inline_text(tmp_path):
    spec = importlib.util.spec_from_file_location("_setup_sender_inline_test", DISPLAY / "send.py")
    assert spec and spec.loader
    with mock.patch.dict(os.environ, {"DND_RUNTIME_DIR": str(tmp_path)}):
        sender = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sender)
    sent = []
    with mock.patch.object(sys, "argv", ["send.py", "--setup-question", "Use point buy or roll?"]), \
         mock.patch.object(sys, "stdin", io.StringIO("")), \
         mock.patch.object(sender, "_post", side_effect=lambda *args: sent.append(args) or True):
        sender.main()
    assert sent[0][0] == sender.SETUP_URL
    assert b"Use point buy or roll?" in sent[0][1]


def test_setup_question_falls_back_during_display_upgrade(tmp_path):
    spec = importlib.util.spec_from_file_location("_setup_sender_fallback_test", DISPLAY / "send.py")
    assert spec and spec.loader
    with mock.patch.dict(os.environ, {"DND_RUNTIME_DIR": str(tmp_path)}):
        sender = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sender)
    sent = []
    with mock.patch.object(sys, "argv", ["send.py", "--setup-question"]), \
         mock.patch.object(sys, "stdin", io.StringIO("Which ruleset?")), \
         mock.patch.object(sender, "_post", side_effect=lambda *args: sent.append(args[0]) or len(sent) == 2):
        sender.main()
    assert sent == [sender.SETUP_URL, sender.FLASK_URL]
