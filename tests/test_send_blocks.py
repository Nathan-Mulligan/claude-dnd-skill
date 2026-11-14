"""One send.py call carries a whole turn's narration, so the table hears it after one model call.

Codex runs a shell command only once the model has written all of it, and every model call costs
2 to 4 seconds before its first token. Sending the player echo, narration, NPC speech and tutor
hint as separate calls cost about 3.5 seconds each.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
SEND = REPO / "skills" / "dnd" / "display" / "send.py"


@pytest.fixture
def sender(tmp_path):
    spec = importlib.util.spec_from_file_location("_send_blocks_test", SEND)
    with mock.patch.dict(os.environ, {"DND_RUNTIME_DIR": str(tmp_path)}):
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def _run(sender, argv, body, urlopen=None):
    posted = []

    def post(url, data, token):
        posted.append((url.rsplit("/", 1)[-1], json.loads(data)))
        return True

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(sys, "argv", ["send.py", *argv]))
        stack.enter_context(mock.patch.object(sys, "stdin", io.StringIO(body)))
        stack.enter_context(mock.patch.object(sender, "_post", side_effect=post))
        stack.enter_context(mock.patch.dict(os.environ, {"DND_ACTIVE_CAMPAIGN": ""}))
        if urlopen is not None:
            stack.enter_context(mock.patch.object(sender.urllib.request, "urlopen", side_effect=urlopen))
        sender.main()
    return posted


TURN = """@player Everybody
We take the satchel and ask who gets it.
@narration
Edrin presses the satchel into Nella's hands.
@npc Edrin Shaw voice=bm_lewis
"The auditor. Nobody else."
@narration
Behind him the horse stamps; the light is going.
@tutor
Brackenford is half a day east.
"""


def test_one_call_sends_every_block_in_order_with_its_own_style(sender):
    chunks = [p for endpoint, p in _run(sender, [], TURN) if endpoint == "chunk"]
    assert [(c.get("player"), c.get("npc"), bool(c.get("tutor"))) for c in chunks] == [
        ("Everybody", None, False), (None, None, False), (None, "Edrin Shaw", False),
        (None, None, False), (None, None, True)]
    assert chunks[0]["text"] == "We take the satchel and ask who gets it."
    assert chunks[2]["text"] == '"The auditor. Nobody else."'
    assert chunks[2]["npc_voice"] == "bm_lewis"
    assert "@" not in "".join(c["text"] for c in chunks)


def test_text_before_the_first_marker_is_narration(sender):
    chunks = _run(sender, [], "The rain thickens.\n@npc Hessa\n\"Twenty silver.\"\n")
    assert [c.get("npc") for _, c in chunks] == [None, "Hessa"]
    assert chunks[0][1]["text"] == "The rain thickens."


def test_scene_cue_rides_on_the_first_narration_block_not_the_player_echo(sender):
    from scene_engine import SceneContext
    scene = SceneContext.parse({"key": "brackenford", "label": "Brackenford", "description": "toll gate at dusk"})
    derived = mock.Mock(setting="town", mood="tense", scene=scene)
    with mock.patch.object(sender, "_derive_scene", return_value=(derived, False)):
        chunks = _run(sender, ["--scene", "Brackenford: the toll gate at dusk"], TURN)
    assert "music" not in chunks[0][1]
    assert chunks[1][1]["music"]["event"] == "scene"
    assert sum("music" in c for _, c in chunks) == 1


def test_stat_flags_still_go_once_after_the_blocks(sender):
    posted = _run(sender, ["--stat-hp", "Nella Reed:9:12"], TURN)
    assert [endpoint for endpoint, _ in posted] == ["chunk"] * 5 + ["stats"]


def test_a_marker_needs_the_whole_line(sender):
    body = "She signs it @npc office, then hands it back.\n"
    chunks = _run(sender, [], body)
    assert len(chunks) == 1 and chunks[0][1]["text"] == body.strip() and "npc" not in chunks[0][1]


def test_an_unknown_voice_on_a_marker_is_refused_before_anything_is_sent(sender, capsys):
    with pytest.raises(SystemExit):
        _run(sender, [], "@npc Hessa voice=not a voice\nHello.\n")
    assert "voice" in capsys.readouterr().err


def test_a_turn_that_ends_on_a_bare_question_still_warns(sender, capsys):
    _run(sender, [], "@player Everybody\nWe wait.\n@narration\nThe gate shuts. What do you do?\n")
    assert "something in motion" in capsys.readouterr().err


def test_content_flags_keep_their_single_block_behaviour(sender):
    chunks = _run(sender, ["--npc", "Hessa"], "@player Everybody\nstill hers\n")
    assert len(chunks) == 1 and chunks[0][1]["npc"] == "Hessa"
    assert chunks[0][1]["text"].startswith("@player")


class _Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode("utf-8"))


def test_dice_request_with_narration_sends_the_stake_first_then_waits_for_the_roll(sender, capsys):
    order = []
    replies = iter([_Response({"request_id": "abc", "pending": ["Nella"]}),
                    _Response({"complete": True, "pending": [], "results": ["Nella rolls 1d20+3: [14] +3 = 17"]})])

    def urlopen(request, *a, **k):
        order.append("dice-request")
        return next(replies)

    posted = []
    with mock.patch.object(sys, "argv", ["send.py", "--dice-request", "--character", "Nella", "--modifier", "3",
                                         "--dc", "13", "--wait"]), \
         mock.patch.object(sys, "stdin", io.StringIO("@player Nella\nI haggle.\n@narration\nDC 13; fail and the price holds.\n")), \
         mock.patch.object(sender, "_post", side_effect=lambda url, data, token: posted.append(json.loads(data))
                           or order.append("chunk") or True), \
         mock.patch.object(sender.urllib.request, "urlopen", side_effect=urlopen), \
         mock.patch.dict(os.environ, {"DND_ACTIVE_CAMPAIGN": ""}):
        sender.main()
    assert order[:3] == ["chunk", "chunk", "dice-request"]
    assert posted[1]["text"] == "DC 13; fail and the price holds."
    assert "Nella rolls 1d20+3: [14] +3 = 17" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="select() on pipes is POSIX only")
def test_dice_request_alone_never_hangs_on_an_open_empty_stdin(sender):
    replies = iter([_Response({"request_id": "abc", "pending": []})])
    read_fd, write_fd = os.pipe()
    try:
        with open(read_fd, encoding="utf-8") as stdin, \
             mock.patch.object(sys, "argv", ["send.py", "--dice-request", "--character", "any"]), \
             mock.patch.object(sys, "stdin", stdin), \
             mock.patch.object(sender, "_post", side_effect=AssertionError("nothing to send")), \
             mock.patch.object(sender.urllib.request, "urlopen", side_effect=lambda *a, **k: next(replies)), \
             mock.patch.dict(os.environ, {"DND_ACTIVE_CAMPAIGN": ""}):
            sender.main()
    finally:
        os.close(write_fd)


def test_a_bad_marker_is_refused_before_the_display_switches_campaign(sender, capsys):
    with mock.patch.object(sender, "_ensure_active_campaign") as ensure, pytest.raises(SystemExit):
        _run(sender, [], "@npc Hessa voice=not a voice\nHello.\n")
    assert not ensure.called


def test_an_unnamed_marker_with_text_after_it_stays_text(sender):
    chunks = _run(sender, [], "@dice Nella rolls 1d20+3 = 17\n")
    assert len(chunks) == 1 and chunks[0][1]["text"] == "@dice Nella rolls 1d20+3 = 17" and "dice" not in chunks[0][1]


def test_a_failed_narration_post_keeps_the_scene_cue_for_the_next_narration(sender):
    from scene_engine import SceneContext
    scene = SceneContext.parse({"key": "ford", "label": "The ford", "description": "a flooded ford at dusk"})
    derived = mock.Mock(setting="river", mood="tense", scene=scene)
    posted, results = [], iter([False, True, True])

    def post(url, data, token):
        posted.append(json.loads(data))
        return next(results)

    with mock.patch.object(sender, "_derive_scene", return_value=(derived, False)), \
         mock.patch.object(sys, "argv", ["send.py", "--scene", "The ford: flooded at dusk"]), \
         mock.patch.object(sys, "stdin", io.StringIO("Water rises.\n@npc Edrin\n\"Back!\"\n@narration\nThe rope holds.\n")), \
         mock.patch.object(sender, "_post", side_effect=post), \
         mock.patch.dict(os.environ, {"DND_ACTIVE_CAMPAIGN": ""}):
        sender.main()
    assert "music" not in posted[1] and "music" in posted[2]


def test_with_a_roll_request_stdout_keeps_only_the_request_id_and_rolls(sender, capsys):
    from scene_engine import SceneContext
    scene = SceneContext.parse({"key": "gate", "label": "Gate", "description": "the toll gate"})
    derived = mock.Mock(setting="town", mood="tense", scene=scene)
    replies = iter([_Response({"request_id": "abc", "pending": []})])
    with mock.patch.object(sender, "_derive_scene", return_value=(derived, False)):
        _run(sender, ["--scene", "Gate: the toll gate", "--dice-request", "--character", "any"],
             "DC 13; fail and the toll doubles.\n", urlopen=lambda *a, **k: next(replies))
    assert capsys.readouterr().out.split() == ["abc"]


def test_a_heredoc_file_on_stdin_is_read_for_a_roll_request(sender, tmp_path):
    body = tmp_path / "heredoc"
    body.write_text("DC 13; fail and the price holds.\n", encoding="utf-8")
    with open(body, encoding="utf-8") as stdin, mock.patch.object(sys, "stdin", stdin):
        assert sender._waiting_stdin() == "DC 13; fail and the price holds.\n"


def test_dev_null_on_stdin_is_no_body(sender):
    with open(os.devnull, encoding="utf-8") as stdin, mock.patch.object(sys, "stdin", stdin):
        assert sender._waiting_stdin() == ""


def test_an_opener_call_carries_the_player_echo_before_the_opener_line(sender):
    chunks = _run(sender, ["--opener"], "@player Everybody\nWe take the satchel.\n@narration\nEdrin's grip loosens.\n")
    assert [(c.get("player"), c.get("opener")) for _, c in chunks] == [("Everybody", None), (None, True)]
