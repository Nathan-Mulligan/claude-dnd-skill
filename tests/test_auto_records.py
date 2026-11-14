"""send.py keeps the turn log and the NPC records itself, so the DM only writes the judgement parts."""
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
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))

import npc_describe  # noqa: E402
import records  # noqa: E402

NOW = 1_800_000_000.0

TURN = """@player Everybody
We hand over the satchel.
@narration
The clerk breaks the seal. Hessa Varn, broad-shouldered and grey at the temples, taps the ledger with an ink-stained finger.
@npc Hessa Varn voice=bf_alice
"Twenty silver, or the wagon stays."
@tutor
Twenty silver is 2 gp.
"""


@pytest.fixture
def camp(tmp_path, monkeypatch):
    root, runtime = tmp_path / "data", tmp_path / "runtime"
    folder = root / "campaigns" / "Fenmoor"
    folder.mkdir(parents=True)
    runtime.mkdir()
    (folder / "state.md").write_text("# Campaign: Fenmoor\n\n## Current Situation\n- **Location:** Brackenford, the toll road\n",
                                     encoding="utf-8")
    (folder / "session-log.md").write_text("# Session Log\n\n## Session 6 — 2026-11-07\n### Recap\nThey left Saltmere.\n",
                                           encoding="utf-8")
    (folder / "calendar.json").write_text(json.dumps({"day": 18, "month": 2, "year": 1247,
                                                      "months": ["Thawmoon", "Duskfall"]}), encoding="utf-8")
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("DND_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("DND_ACTIVE_CAMPAIGN", "Fenmoor")
    monkeypatch.setattr(npc_describe, "kick", lambda: None)
    return folder


def blocks(text=TURN):
    sender = load_sender()
    return sender.parse_blocks(text)


def load_sender():
    spec = importlib.util.spec_from_file_location("_auto_records_send", DISPLAY / "send.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def log_text(folder):
    return (folder / "turn-log.md").read_text(encoding="utf-8")


# ── the turn log ─────────────────────────────────────────────────────────────

def test_a_send_is_logged_word_for_word_under_this_session(camp):
    assert records.record_send(blocks(), place="Brackenford toll gate", now=NOW) is None
    text = log_text(camp)
    assert text.startswith("# Turn log\n")
    assert "\n## Session 7 · " in text
    assert "· Brackenford toll gate · 18 Duskfall 1247\n" in text
    assert "**Everybody:** We hand over the satchel." in text
    assert "**DM:** The clerk breaks the seal. Hessa Varn, broad-shouldered" in text
    assert '**Hessa Varn:** "Twenty silver, or the wagon stays."' in text
    assert "**DM hint:** Twenty silver is 2 gp." in text


def test_later_sends_join_the_same_session_until_a_long_gap(camp):
    records.record_send(blocks(), place="Gate", now=NOW)
    records.record_send(blocks("The rain thickens.\n"), now=NOW + 600)
    assert log_text(camp).count("## Session ") == 1
    assert "· Gate · " in log_text(camp).split("The rain thickens")[0].rsplit("###", 1)[1]
    records.record_send(blocks("Morning comes.\n"), now=NOW + records.SESSION_GAP_S + 601)
    assert log_text(camp).count("## Session 7 · ") == 2


def test_a_replayed_send_is_not_logged_twice(camp):
    records.record_send(blocks(), now=NOW)
    records.record_send([b for b in blocks() if b["kind"] is None], now=NOW + 60)
    assert log_text(camp).count("The clerk breaks the seal") == 1


def test_place_falls_back_to_state_md_and_session_to_the_count(camp):
    (camp / "session-log.md").write_text("# Session Log\n\n## Session X — <date>\n", encoding="utf-8")
    (camp / "state.md").write_text("**Session count:** 2\n## Current Situation\n- **Location:** Saltmere docks\n",
                                   encoding="utf-8")
    records.record_send(blocks("Gulls wheel.\n"), now=NOW)
    assert "## Session 3 · " in log_text(camp) and "· Saltmere docks · " in log_text(camp)


def test_a_heading_inside_narration_cannot_start_a_section(camp):
    records.record_send(blocks("The sign reads:\n## Session 99\n"), now=NOW)
    assert "\n\\## Session 99" in log_text(camp) and "\n## Session 99" not in log_text(camp)


def test_awards_are_logged(camp):
    records.record_send([], awards=[("XP", "Nella, Elbane — 250 XP (Toll paid)")], now=NOW)
    assert "**XP:** Nella, Elbane — 250 XP (Toll paid)" in log_text(camp)


def test_no_campaign_means_no_files(camp, monkeypatch):
    monkeypatch.setenv("DND_ACTIVE_CAMPAIGN", "")
    assert records.record_send(blocks(), now=NOW) is None
    assert not (camp / "turn-log.md").exists()


def test_the_display_campaign_is_used_without_an_active_campaign(camp, monkeypatch):
    monkeypatch.setenv("DND_ACTIVE_CAMPAIGN", "")
    Path(os.environ["DND_RUNTIME_DIR"], ".campaign").write_text("Fenmoor\n", encoding="utf-8")
    records.record_send(blocks(), now=NOW)
    assert (camp / "turn-log.md").exists()


# ── NPC records ──────────────────────────────────────────────────────────────

def test_a_new_npc_gets_where_they_were_met_and_their_voice(camp):
    records.record_send(blocks(), place="Brackenford toll gate", now=NOW)
    text = (camp / "npcs.md").read_text(encoding="utf-8")
    assert "### Hessa Varn\n" in text
    assert "- **Met:** Brackenford toll gate, session 7 (18 Duskfall 1247)" in text
    assert "- **Voice:** bf_alice" in text


def test_the_dms_own_lines_are_never_changed(camp):
    dm_entry = ("# NPCs\n\n### Hessa Varn\n- **Role:** toll clerk\n- **Voice:** gravel and patience\n"
                "- **Demeanor:** tired, exact\n\nShe once worked the Saltmere docks.\n\n### Edrin Shaw\n- **Role:** carter\n")
    (camp / "npcs.md").write_text(dm_entry, encoding="utf-8")
    records.record_send(blocks(), place="Brackenford toll gate", now=NOW)
    records.record_send(blocks(TURN.replace("voice=bf_alice", "voice=af_heart")), place="Elsewhere", now=NOW + 60)
    text = (camp / "npcs.md").read_text(encoding="utf-8")
    for line in dm_entry.splitlines():
        assert line in text
    assert text.count("- **Voice:**") == 1
    assert text.count("- **Met:**") == 1 and "Elsewhere" not in text
    assert text.index("- **Met:**") < text.index("She once worked")


def test_a_first_name_finds_the_one_entry_it_belongs_to(camp):
    (camp / "npcs.md").write_text("# NPCs\n\n### Hessa Varn\n- **Role:** toll clerk\n", encoding="utf-8")
    records.record_send(blocks('@npc Hessa\n"Next."\n'), place="Gate", now=NOW)
    text = (camp / "npcs.md").read_text(encoding="utf-8")
    assert "### Hessa\n" not in text and "- **Met:** Gate, session 7" in text


def test_the_voice_the_table_hears_comes_from_the_cast(camp):
    (camp / "cast.json").write_text(json.dumps({"hessa varn": "bf_emma"}), encoding="utf-8")
    records.record_send(blocks('@npc Hessa Varn\n"Next."\n'), now=NOW)
    assert "- **Voice:** bf_emma" in (camp / "npcs.md").read_text(encoding="utf-8")


def test_an_entry_kept_in_npcs_full_is_filled_there(camp):
    (camp / "npcs.md").write_text("# NPCs\n\n| Name | Role |\n|---|---|\n| Hessa Varn | clerk |\n", encoding="utf-8")
    (camp / "npcs-full.md").write_text("# NPCs\n\n### Hessa Varn\n- **Role:** toll clerk\n", encoding="utf-8")
    records.record_send(blocks(), now=NOW)
    assert "- **Met:**" in (camp / "npcs-full.md").read_text(encoding="utf-8")
    assert "### Hessa Varn" not in (camp / "npcs.md").read_text(encoding="utf-8")


def test_a_lower_case_speaker_is_not_given_an_entry(camp):
    records.record_send(blocks('@npc a guard\n"Move along."\n'), now=NOW)
    assert not (camp / "npcs.md").exists()


def test_a_broken_npc_file_never_fails_the_send(camp):
    (camp / "npcs.md").mkdir()
    problem = records.record_send(blocks(), now=NOW)
    assert problem and "not written" in problem
    assert "We hand over the satchel." in log_text(camp)


# ── looks and manner, copied by the helper model ─────────────────────────────

def test_the_helper_is_asked_once_for_an_npc_without_a_look(camp, monkeypatch):
    monkeypatch.setattr(npc_describe, "_choice", lambda: ("codex", "gpt-6-luna"))
    records.record_send(blocks(), now=NOW)
    rows = npc_describe._read_queue()
    assert [r["name"] for r in rows] == ["Hessa Varn"]
    assert "broad-shouldered" in rows[0]["text"] and "Twenty silver" not in rows[0]["text"]


def test_no_helper_means_no_queue(camp, monkeypatch):
    monkeypatch.setattr(npc_describe, "_choice", lambda: None)
    records.record_send(blocks(), now=NOW)
    assert npc_describe._read_queue() == []


def test_an_npc_the_dm_already_described_is_not_queued(camp, monkeypatch):
    monkeypatch.setattr(npc_describe, "_choice", lambda: ("codex", "gpt-6-luna"))
    (camp / "npcs.md").write_text("# NPCs\n\n### Hessa Varn\n- **Look:** tall, scarred\n", encoding="utf-8")
    records.record_send(blocks(), now=NOW)
    assert npc_describe._read_queue() == []


def _answer(phrases):
    return lambda prompt, schema, choice=None: {"phrases": phrases}


def test_only_phrases_copied_from_the_narration_are_kept_and_added_on_the_next_send(camp, monkeypatch):
    monkeypatch.setattr(npc_describe, "_choice", lambda: ("codex", "gpt-6-luna"))
    import lore_sync
    monkeypatch.setattr(lore_sync, "ask", _answer(["broad-shouldered and grey at the temples",
                                                  "a kind smile",  # invented: not in the narration
                                                  "taps the ledger with an ink-stained finger"]))
    records.record_send(blocks(), place="Gate", now=NOW)
    assert npc_describe.process(npc_describe._take_queue()) == 1
    assert "As seen" not in (camp / "npcs.md").read_text(encoding="utf-8")  # the DM's next send applies it
    records.record_send(blocks("The gate creaks.\n"), now=NOW + 60)
    text = (camp / "npcs.md").read_text(encoding="utf-8")
    assert ("- **As seen:** broad-shouldered and grey at the temples; taps the ledger with an ink-stained finger"
            in text)
    assert "kind smile" not in text
    records.record_send(blocks(), now=NOW + 120)
    assert npc_describe._read_queue() == []


def test_nothing_said_means_nothing_written_and_tries_are_capped(camp, monkeypatch):
    monkeypatch.setattr(npc_describe, "_choice", lambda: ("codex", "gpt-6-luna"))
    import lore_sync
    asked = []
    monkeypatch.setattr(lore_sync, "ask", lambda *a, **k: asked.append(1) or {"phrases": []})
    for i in range(npc_describe.MAX_TRIES + 2):
        records.record_send(blocks(TURN.replace("Twenty", f"Twenty{i}")), now=NOW + i)
        npc_describe.process(npc_describe._take_queue())
    assert len(asked) == npc_describe.MAX_TRIES
    assert "As seen" not in (camp / "npcs.md").read_text(encoding="utf-8")


def test_a_look_the_dm_wrote_meanwhile_wins_over_the_helper(camp, monkeypatch):
    monkeypatch.setattr(npc_describe, "_choice", lambda: ("codex", "gpt-6-luna"))
    import lore_sync
    monkeypatch.setattr(lore_sync, "ask", _answer(["broad-shouldered and grey at the temples"]))
    records.record_send(blocks(), now=NOW)
    npc_describe.process(npc_describe._take_queue())
    path = camp / "npcs.md"
    path.write_text(path.read_text(encoding="utf-8").replace("- **Met:**", "- **Look:** a heron of a woman\n- **Met:**"),
                    encoding="utf-8")
    records.record_send(blocks("The gate creaks.\n"), now=NOW + 60)
    text = path.read_text(encoding="utf-8")
    assert "a heron of a woman" in text and "As seen" not in text


def test_a_failing_helper_is_logged_and_writes_nothing(camp, monkeypatch):
    monkeypatch.setattr(npc_describe, "_choice", lambda: ("codex", "gpt-6-luna"))
    import lore_sync

    def boom(*a, **k):
        raise RuntimeError("codex exited 1")

    monkeypatch.setattr(lore_sync, "ask", boom)
    records.record_send(blocks(), now=NOW)
    assert npc_describe.process(npc_describe._take_queue()) == 0
    assert "model failed" in Path(os.environ["DND_RUNTIME_DIR"], "npc_describe.log").read_text(encoding="utf-8")


def test_the_prompt_asks_to_copy_never_invent():
    assert "word for word" in npc_describe.PROMPT and "Never add, guess" in npc_describe.PROMPT


# ── send.py does it after delivery ───────────────────────────────────────────

def _send(argv, body, ok=lambda payload: True):
    sender = load_sender()
    posted = []

    def post(url, data, token):
        payload = json.loads(data)
        posted.append(payload)
        return ok(payload)

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(sys, "argv", ["send.py", *argv]))
        stack.enter_context(mock.patch.object(sys, "stdin", io.StringIO(body)))
        stack.enter_context(mock.patch.object(sender, "_post", side_effect=post))
        stack.enter_context(mock.patch.object(sender, "_ensure_active_campaign", lambda: None))
        sender.main()
    return posted


def test_send_logs_the_turn_after_it_reaches_the_display(camp):
    _send([], TURN)
    assert "**Everybody:** We hand over the satchel." in log_text(camp)
    assert "- **Voice:** bf_alice" in (camp / "npcs.md").read_text(encoding="utf-8")


def test_send_does_not_log_a_block_the_display_never_got(camp):
    _send([], TURN, ok=lambda payload: not payload.get("npc"))
    text = log_text(camp)
    assert "We hand over the satchel." in text and "Twenty silver, or" not in text
    assert not (camp / "npcs.md").exists()


def test_send_records_the_scene_place(camp):
    from scene_engine import SceneContext
    sender = load_sender()
    scene = SceneContext.parse({"key": "brackenford-gate", "label": "Brackenford toll gate", "description": "dusk"})
    derived = mock.Mock(setting="town", mood="tense", scene=scene)
    with mock.patch.object(sys, "argv", ["send.py", "--scene", "Brackenford toll gate: dusk"]), \
         mock.patch.object(sys, "stdin", io.StringIO(TURN)), \
         mock.patch.object(sender, "_post", return_value=True), \
         mock.patch.object(sender, "_ensure_active_campaign", lambda: None), \
         mock.patch.object(sender, "_derive_scene", return_value=(derived, False)):
        sender.main()
    assert "- **Met:** Brackenford toll gate, session 7" in (camp / "npcs.md").read_text(encoding="utf-8")


def test_send_flag_form_npc_is_recorded_too(camp):
    _send(["--npc", "Edrin Shaw", "--npc-voice", "bm_lewis"], '"The auditor. Nobody else."\n')
    assert "- **Voice:** bm_lewis" in (camp / "npcs.md").read_text(encoding="utf-8")


def test_send_logs_an_xp_award(camp):
    _send(["--xp-award", json.dumps({"names": ["Nella"], "xp": 50, "reason": "Toll paid"})], "")
    assert "**XP:** Nella — 50 XP (Toll paid)" in log_text(camp)


def test_a_records_failure_is_one_quiet_line_and_the_send_still_succeeds(camp, capsys):
    (camp / "npcs.md").mkdir()
    _send([], TURN)
    err = capsys.readouterr().err
    assert "send.py: turn log or NPC record not written" in err


def test_the_worker_drains_the_queue_and_a_second_one_stands_aside(camp, monkeypatch):
    monkeypatch.setattr(npc_describe, "_choice", lambda: ("codex", "gpt-6-luna"))
    import lore_sync
    monkeypatch.setattr(lore_sync, "ask", _answer(["broad-shouldered and grey at the temples"]))
    kicks = []
    monkeypatch.setattr(npc_describe, "kick", lambda: kicks.append(1))
    records.record_send(blocks(), now=NOW)
    assert npc_describe.run() == "found 1"
    assert npc_describe._read_queue() == [] and kicks == [1]  # the request's own kick, nothing after

    fcntl = pytest.importorskip("fcntl")
    fd = os.open(npc_describe.rt("npc-describe.lock"), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        Path(npc_describe.queue_path()).write_text('{"folder": "x", "name": "Y", "text": "z"}\n', encoding="utf-8")
        assert npc_describe.run() == "busy" and kicks == [1]
    finally:
        os.close(fd)
