"""The notes helper writes the Save & close summary: only what happened, nothing lost."""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "skills/dnd/display"))
spec = importlib.util.spec_from_file_location("session_summary", REPO / "skills/dnd/display/session_summary.py")
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)

STATE = """# Campaign: Test
**Created:** 2026-01-01  **Last session:** 2026-11-01  **Session count:** 7

## Next Hooks
- SECRET-HOOK: the courier carries the auditor's notice.

## Clocks
- SECRET-CLOCK: the bridge closes on the 21st.

## Story So Far
*(One paragraph, at most 150 words, for everything older than the Continuity Archive.)*

## Continuity Archive
*Compressed note stays.*

### Session 1 — 2026-01-01 — Brackenford
- Met Hobb the ferryman.

### Session 2 — 2026-01-08 — The toll road
- Paid the Wardens 4 gp.

## Recent Events
- Sold the flour to Marta at 18 sp.

## DM Notes (hidden from players)
SECRET-NOTE: Marta is the auditor's sister.
"""

SESSION_6 = """## Session 6 — 2026-11-01
### Recap
The party reached Brackenford and met Marta.

### DM Calibration
- **What worked:** SECRET-CALIBRATION pacing.

---

"""

LOG = """# Session Log — Test

## Session 7 — 2026-11-08
### Recap
They crossed the fen with Hobb.

### Key Events
- Hobb asked for 2 gp.
"""

LOG_TWO = LOG.replace("## Session 7", SESSION_6 + "## Session 7")

TAIL_NOTE = """# Where we stopped
At the Brackenford mill.

**Waiting on:** nothing

## Last exchanges, oldest first

**Players:** We knock.

<!-- display-mark: {mark} -->
"""

OLD_LINE = {"text": "They crossed the fen.", "_camp": "test"}
NEW_LINES = [
    {"player": "Elbane", "text": "I ask the miller about the toll."},
    {"text": "The miller, Corwin Ash, wipes flour from his hands and names the price: 3 gp a wagon."},
    {"npc": "Corwin Ash", "text": "Three gold, and the Wardens take a third."},
]

GOOD = {
    "recap": "Elbane asked Corwin Ash about the toll. Corwin named 3 gp a wagon and said the Wardens take a third.",
    "key_events": ["Corwin Ash charges 3 gp a wagon.", "The Wardens take a third of the toll."],
    "archived": [],
    "story_so_far": "",
}


@pytest.fixture(autouse=True)
def own_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path / "runtime-default"))


def campaign(tmp_path, state=STATE, log=LOG, lines=None, marked=True):
    folder = tmp_path / "test"
    folder.mkdir()
    (folder / "state.md").write_text(state, encoding="utf-8")
    if log is not None:
        (folder / "session-log.md").write_text(log, encoding="utf-8")
    shown = [OLD_LINE] + (NEW_LINES if lines is None else lines)
    (folder / "session_tail.json").write_text(json.dumps(shown), encoding="utf-8")
    mark = summary.stop_point.fingerprint(OLD_LINE) if marked else "0" * 16
    (folder / "session-tail.md").write_text(TAIL_NOTE.format(mark=mark), encoding="utf-8")
    return folder


def stub_helper(monkeypatch, reply=GOOD):
    prompts = []
    monkeypatch.setattr(summary, "ask", lambda prompt, choice: prompts.append(prompt) or json.loads(json.dumps(reply)))
    return prompts


def run(folder, monkeypatch, reply=GOOD, today="2026-11-14"):
    prompts = stub_helper(monkeypatch, reply)
    mark = summary.display_mark(folder)
    plan = summary.prepare(folder, mark, ("codex", "gpt-6-luna"), today=today)
    summary.write(folder, plan)
    return prompts, plan


def read(folder, name):
    return (folder / name).read_text(encoding="utf-8")


# ── what the helper is shown ────────────────────────────────────────────────

def test_prompt_holds_this_sessions_lines_and_no_dm_secrets(tmp_path, monkeypatch):
    folder = campaign(tmp_path)
    [prompt], _ = run(folder, monkeypatch)
    assert "Corwin Ash" in prompt and "names the price: 3 gp a wagon" in prompt
    assert "Elbane: I ask the miller" in prompt
    assert "Sold the flour to Marta" in prompt  # Recent Events is party-known
    assert "They crossed the fen with Hobb." in prompt  # last recap, marked as already recorded
    assert "They crossed the fen.\n" not in prompt  # display line from before the last saved stop point
    assert "SECRET" not in prompt
    assert "display-mark" not in prompt


def test_display_lines_are_all_used_when_the_old_mark_is_gone(tmp_path, monkeypatch):
    folder = campaign(tmp_path, marked=False)
    [prompt], _ = run(folder, monkeypatch)
    assert "They crossed the fen." in prompt and "Corwin Ash" in prompt


# ── session-log.md ──────────────────────────────────────────────────────────

def test_a_new_session_entry_gets_the_recap_and_key_events(tmp_path, monkeypatch):
    folder = campaign(tmp_path)
    run(folder, monkeypatch)
    log = read(folder, "session-log.md")
    assert log.startswith(LOG.rstrip("\n"))
    new = log[log.index("## Session 8 — 2026-11-14"):]
    assert "### Recap\nElbane asked Corwin Ash about the toll." in new
    assert "### Key Events\n- Corwin Ash charges 3 gp a wagon.\n- The Wardens take a third of the toll." in new


def test_an_open_entry_for_this_session_is_filled_in_place(tmp_path, monkeypatch):
    open_entry = LOG + "\n## Session 8 — 2026-11-14\n**Location:** Brackenford\n\n### Turns\n- Elbane asked about the toll.\n"
    folder = campaign(tmp_path, log=open_entry)
    [prompt], _ = run(folder, monkeypatch)
    assert "- Elbane asked about the toll." in prompt
    log = read(folder, "session-log.md")
    assert log.count("## Session 8") == 1
    entry = log[log.index("## Session 8"):]
    assert entry.index("**Location:** Brackenford") < entry.index("### Recap\nElbane asked") < entry.index("### Turns")
    assert "- Elbane asked about the toll." in entry


def test_a_placeholder_recap_is_replaced(tmp_path, monkeypatch):
    open_entry = LOG + "\n## Session 8 — 2026-11-14\n### Recap\n<3-5 sentence summary of what happened>\n\n### Key Events\n- \n"
    folder = campaign(tmp_path, log=open_entry)
    run(folder, monkeypatch)
    entry = read(folder, "session-log.md").split("## Session 8")[1]
    assert "<3-5 sentence" not in entry
    assert entry.count("### Recap") == 1 and entry.count("### Key Events") == 1
    assert "- Corwin Ash charges 3 gp a wagon." in entry


def test_a_missing_session_log_is_created(tmp_path, monkeypatch):
    folder = campaign(tmp_path, log=None)
    run(folder, monkeypatch)
    assert "## Session 3 — 2026-11-14\n\n### Recap\nElbane asked" in read(folder, "session-log.md")  # after the archive's Session 2


def test_nothing_new_this_session_writes_nothing(tmp_path, monkeypatch):
    folder = campaign(tmp_path, lines=[])
    monkeypatch.setattr(summary, "ask", lambda prompt, choice: pytest.fail("Nothing to summarise"))
    plan = summary.prepare(folder, summary.display_mark(folder), ("codex", "m"))
    assert plan == {}
    summary.write(folder, plan)
    assert read(folder, "session-log.md") == LOG


# ── archive rotation and the Story So Far fold ──────────────────────────────

ARCHIVED = {"session": 6, "label": "Brackenford", "bullets": ["The party reached Brackenford.", "They met Marta."]}


def test_entries_older_than_the_newest_two_move_to_the_archive(tmp_path, monkeypatch):
    folder = campaign(tmp_path, log=LOG_TWO)
    [prompt], _ = run(folder, monkeypatch, dict(GOOD, archived=[ARCHIVED]))
    assert "Session 6" in prompt and "The party reached Brackenford and met Marta." in prompt
    assert "SECRET" not in prompt
    log = read(folder, "session-log.md")
    assert "## Session 6" not in log and "## Session 7" in log and "## Session 8" in log
    archive = read(folder, "session-log-archive.md")
    assert "## Session 6 — 2026-11-01" in archive and "SECRET-CALIBRATION" in archive  # moved whole, never lost
    state = read(folder, "state.md")
    section = state[state.index("## Continuity Archive"):state.index("## Recent Events")]
    assert "### Session 6 — 2026-11-01 — Brackenford\n- The party reached Brackenford.\n- They met Marta." in section
    assert section.index("### Session 2") < section.index("### Session 6")
    assert "*Compressed note stays.*" in section


def test_archive_beyond_five_folds_into_story_so_far(tmp_path, monkeypatch):
    sessions = "".join(f"### Session {n} — 2026-0{n}-01 — Place {n}\n- Fact {n}.\n\n" for n in range(1, 6))
    state = STATE.replace(STATE[STATE.index("### Session 1"):STATE.index("## Recent Events")], sessions)
    folder = campaign(tmp_path, state=state, log=LOG_TWO)
    story = "Hobb ferried the party across the fen, and the Wardens took their toll at Brackenford."
    [prompt], _ = run(folder, monkeypatch, dict(GOOD, archived=[ARCHIVED], story_so_far=story))
    assert "- Fact 1." in prompt
    state = read(folder, "state.md")
    assert f"## Story So Far\n{story}\n" in state
    assert "### Session 1 " not in state and "### Session 2 " in state and "### Session 6 " in state
    archive = read(folder, "session-log-archive.md")
    assert "### Session 1 — 2026-01-01 — Place 1\n- Fact 1." in archive
    assert archive.index("## Session 6") < archive.index("### Session 1")


def test_a_missing_continuity_archive_is_created_after_story_so_far(tmp_path, monkeypatch):
    state = STATE[:STATE.index("## Continuity Archive")] + STATE[STATE.index("## Recent Events"):]
    folder = campaign(tmp_path, state=state, log=LOG_TWO)
    run(folder, monkeypatch, dict(GOOD, archived=[ARCHIVED]))
    state = read(folder, "state.md")
    assert state.index("## Story So Far") < state.index("## Continuity Archive\n") < state.index("## Recent Events")
    assert "### Session 6 — 2026-11-01 — Brackenford" in state


# ── replies that are refused ────────────────────────────────────────────────

@pytest.mark.parametrize("reply", [
    None, "text", {"recap": "x"},
    dict(GOOD, recap=""),
    dict(GOOD, key_events="Corwin"),
    dict(GOOD, extra=1),
    dict(GOOD, archived=[{"session": 5, "label": "x", "bullets": ["Marta."]}]),  # no session moves out
])
def test_malformed_replies_are_refused(tmp_path, monkeypatch, reply):
    folder = campaign(tmp_path)
    stub_helper(monkeypatch, reply)
    with pytest.raises(ValueError):
        summary.prepare(folder, summary.display_mark(folder), ("codex", "m"))


def test_archive_reply_must_cover_every_session_moving_out(tmp_path, monkeypatch):
    folder = campaign(tmp_path, log=LOG_TWO)
    stub_helper(monkeypatch, GOOD)  # Session 6 must move, but no bullets came back
    with pytest.raises(ValueError, match="Session 6"):
        summary.prepare(folder, summary.display_mark(folder), ("codex", "m"))


def test_a_long_story_so_far_is_refused(tmp_path, monkeypatch):
    sessions = "".join(f"### Session {n} — Place\n- Fact {n}.\n\n" for n in range(1, 6))
    state = STATE.replace(STATE[STATE.index("### Session 1"):STATE.index("## Recent Events")], sessions)
    folder = campaign(tmp_path, state=state, log=LOG_TWO)
    stub_helper(monkeypatch, dict(GOOD, archived=[ARCHIVED], story_so_far="Hobb ferried them. " * 60))
    with pytest.raises(ValueError, match="150 words"):
        summary.prepare(folder, summary.display_mark(folder), ("codex", "m"))


def test_a_name_the_material_never_mentions_is_refused(tmp_path, monkeypatch):
    folder = campaign(tmp_path)
    invented = dict(GOOD, recap="Elbane asked Corwin Ash about the toll, and Lady Veyra watched from the bridge.")
    stub_helper(monkeypatch, invented)
    with pytest.raises(ValueError, match="Veyra"):
        summary.prepare(folder, summary.display_mark(folder), ("codex", "m"))


def test_ordinary_sentence_openers_are_not_mistaken_for_names(tmp_path, monkeypatch):
    folder = campaign(tmp_path)
    reply = dict(GOOD, recap="Meanwhile Elbane asked Corwin Ash about the toll. Afterwards they waited.")
    run(folder, monkeypatch, reply)
    assert "Meanwhile Elbane asked" in read(folder, "session-log.md")


# ── writing ─────────────────────────────────────────────────────────────────

def test_write_refuses_files_changed_since_the_plan(tmp_path, monkeypatch):
    folder = campaign(tmp_path, log=LOG_TWO)
    stub_helper(monkeypatch, dict(GOOD, archived=[ARCHIVED]))
    plan = summary.prepare(folder, summary.display_mark(folder), ("codex", "m"))
    (folder / "state.md").write_text(STATE + "- DM edit\n", encoding="utf-8")
    with pytest.raises(ValueError, match="state.md"):
        summary.write(folder, plan)
    assert read(folder, "session-log.md") == LOG_TWO
    assert not (folder / "session-log-archive.md").exists()


def test_helper_is_off_when_lore_sync_is_off(monkeypatch):
    monkeypatch.setenv("DND_LORE_SYNC", "0")
    assert summary.helper() is None
    monkeypatch.setenv("DND_LORE_SYNC", "1")
    monkeypatch.setenv("DND_LORE_MODEL", "luna")
    assert summary.helper() == ("codex", "gpt-6-luna")


def test_ask_uses_the_shared_helper_with_the_summary_schema(monkeypatch):
    seen = {}

    def fake(prompt, model, **options):
        seen.update(model=model, **options)
        return GOOD

    monkeypatch.setitem(summary.lore_sync._PROVIDERS, "codex", fake)
    assert summary.ask("p", ("codex", "gpt-6-luna")) == GOOD
    assert seen["model"] == "gpt-6-luna"
    assert seen["schema"] is summary.SCHEMA
    assert seen["timeout"] == summary.SUMMARY_TIMEOUT_S


# ── from Sol's review ───────────────────────────────────────────────────────

def test_dm_only_lines_in_moving_text_stay_out_of_the_prompt_but_are_kept(tmp_path, monkeypatch):
    sessions = "".join(f"### Session {n} — Place {n}\n- Fact {n}.\n\n" for n in range(2, 6))
    secret_fold = ("### Session 1 — Place 1\n- Fact 1.\n- DM note: SECRET-FOLD-A.\n- Off-screen, SECRET-FOLD-B.\n"
                   "- The Wardens' faction move: SECRET-FOLD-C.\n\n")
    state = STATE.replace(STATE[STATE.index("### Session 1"):STATE.index("## Recent Events")], secret_fold + sessions)
    log = LOG_TWO.replace("The party reached Brackenford and met Marta.",
                          "The party reached Brackenford and met Marta.\nHidden from the party: SECRET-ROTATE.")
    folder = campaign(tmp_path, state=state, log=log)
    [prompt], _ = run(folder, monkeypatch, dict(GOOD, archived=[ARCHIVED], story_so_far="Fact 1 came first."))
    assert "SECRET" not in prompt and "- Fact 1." in prompt
    archive = read(folder, "session-log-archive.md")
    for kept in ("SECRET-FOLD-A", "SECRET-FOLD-B", "SECRET-FOLD-C", "SECRET-ROTATE", "SECRET-CALIBRATION"):
        assert kept in archive


def test_the_whole_session_comes_from_the_display_text_log(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("DND_RUNTIME_DIR", str(runtime))
    early = {"text": "Hobb gave the party the sealed letter.", "_camp": "test"}
    filler = [{"text": f"The party waited by the mill, turn {i}.", "_camp": "test"} for i in range(40)]
    other = {"text": "OTHER-CAMPAIGN line.", "_camp": "elsewhere"}
    stamped_old = dict(OLD_LINE)
    (runtime / "text_log.json").write_text(json.dumps([stamped_old, early, other] + filler), encoding="utf-8")
    folder = campaign(tmp_path, lines=filler[-29:])
    (folder / "session_tail.json").write_text(json.dumps(filler[-30:]), encoding="utf-8")  # the old mark has scrolled out
    reply = dict(GOOD, recap="Hobb gave the party the sealed letter, and they waited by the mill.",
                 key_events=["Hobb gave the party the sealed letter."])
    [prompt], _ = run(folder, monkeypatch, reply)
    assert "Hobb gave the party the sealed letter." in prompt
    assert "turn 0." in prompt and "turn 39." in prompt
    assert "OTHER-CAMPAIGN" not in prompt and "They crossed the fen.\n" not in prompt


def test_a_number_the_material_never_gives_is_refused(tmp_path, monkeypatch):
    folder = campaign(tmp_path)
    stub_helper(monkeypatch, dict(GOOD, key_events=["Corwin Ash charges 300 gp a wagon."]))
    with pytest.raises(ValueError, match="300"):
        summary.prepare(folder, summary.display_mark(folder), ("codex", "m"))


# ── this session's turns from turn-log.md (kept by send.py) ─────────────────

TURNS_7 = "## Session 7 · 2026-11-08\n\n### 20:00 · The fen\n\n**DM:** TURNLOG-SEVEN Hobb poles the ferry.\n"
TURNS_8 = """## Session 8 · 2026-11-14

### 21:04 · Brackenford mill · 18 Duskfall 1247

**Elbane:** I ask the miller about the toll.

**DM:** The miller, Corwin Ash, wipes flour from his hands and names the price: 3 gp a wagon.

**Corwin Ash:** Three gold, and the Wardens take a third.

**DM hint:** TUTOR-LINE a check would help.
"""
TURNS_8_LATER = "## Session 8 · 2026-11-14\n\n### 23:30 · Brackenford mill\n\n**DM:** AFTER-THE-BREAK the mill falls quiet.\n"


def turn_log(folder, *sections, older_than_log=False):
    path = folder / "turn-log.md"
    path.write_text("# Turn log\n\n" + "\n".join(sections), encoding="utf-8")
    log = folder / "session-log.md"
    if log.exists():
        stamp = log.stat().st_mtime_ns + (-10 if older_than_log else 10) * 1_000_000_000
        os.utime(path, ns=(stamp, stamp))


def test_this_sessions_turns_come_from_the_turn_log(tmp_path, monkeypatch):
    folder = campaign(tmp_path, lines=[])  # the display's own log shows nothing new
    turn_log(folder, TURNS_7, TURNS_8)
    [prompt], _ = run(folder, monkeypatch)
    assert "Corwin Ash: Three gold, and the Wardens take a third." in prompt
    assert "Elbane: I ask the miller about the toll." in prompt
    assert "TURNLOG-SEVEN" not in prompt and "TUTOR-LINE" not in prompt
    log = read(folder, "session-log.md")
    assert log.count("## Session 8") == 1 and "## Session 8 — 2026-11-14\n\n### Recap\nElbane asked" in log
    assert summary.records.session_number(folder) == 9  # send.py's next section follows the summary


def test_a_session_split_by_a_long_break_is_read_whole(tmp_path, monkeypatch):
    folder = campaign(tmp_path, lines=[])
    turn_log(folder, TURNS_7, TURNS_8, TURNS_8_LATER)
    [prompt], _ = run(folder, monkeypatch)
    assert "Corwin Ash: Three gold" in prompt and "AFTER-THE-BREAK" in prompt
    assert read(folder, "session-log.md").count("## Session 8") == 1


def test_a_recap_written_mid_session_is_replaced_by_the_whole_session(tmp_path, monkeypatch):
    switched = LOG + "\n## Session 8 — 2026-11-14\n### Recap\nDM-SWITCH-RECAP before the model change.\n"
    folder = campaign(tmp_path, log=switched, lines=[])
    turn_log(folder, TURNS_7, TURNS_8)  # turns went on after the DM's recap
    run(folder, monkeypatch)
    log = read(folder, "session-log.md")
    assert log.count("## Session 8") == 1 and "## Session 9" not in log
    entry = log[log.index("## Session 8"):]
    assert "### Recap\nElbane asked Corwin Ash" in entry and "DM-SWITCH-RECAP" not in entry


def test_a_turn_log_older_than_the_last_summary_is_not_reused(tmp_path, monkeypatch):
    folder = campaign(tmp_path)  # the display showed this session's lines
    turn_log(folder, TURNS_7, older_than_log=True)
    [prompt], _ = run(folder, monkeypatch)
    assert "TURNLOG-SEVEN" not in prompt and "Elbane: I ask the miller" in prompt
    assert "## Session 8 — 2026-11-14" in read(folder, "session-log.md")


def test_the_import_entry_is_not_taken_for_this_session(tmp_path, monkeypatch):
    state = STATE[:STATE.index("## Continuity Archive")] + STATE[STATE.index("## Recent Events"):]
    state = state.replace("**Session count:** 7", "**Session count:** 0")
    imported = "# Session Log — Test\n\n## Session 0 — Import — 2026-11-01\nSource: module.pdf\n"
    folder = campaign(tmp_path, state=state, log=imported, lines=[])
    turn_log(folder, TURNS_8.replace("Session 8", "Session 1"))
    run(folder, monkeypatch)
    log = read(folder, "session-log.md")
    assert log.startswith(imported) and "## Session 1 — 2026-11-14\n\n### Recap\nElbane asked" in log


def test_session_numbers_agree_with_the_archive(tmp_path):
    folder = campaign(tmp_path, log=None)  # only the Continuity Archive (Sessions 1 and 2) remains
    assert summary.records.session_number(folder) == summary.session_number(folder) == 3


def test_send_and_summary_agree_on_the_session_number(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "campaigns").mkdir(parents=True)
    folder = campaign(root / "campaigns", lines=[])
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("DND_ACTIVE_CAMPAIGN", "test")
    monkeypatch.setattr(summary.records.npc_describe, "kick", lambda: None)
    now = 1_800_000_000.0
    shown = [{"kind": "player", "name": "Elbane", "text": "I ask the miller about the toll."},
             {"text": "The miller, Corwin Ash, names the price: 3 gp a wagon."}]
    assert summary.records.record_send(shown, place="Brackenford mill", now=now) is None
    assert "\n## Session 8 · " in read(folder, "turn-log.md")
    assert summary.session_number(folder) == 8
    stamp = (folder / "session-log.md").stat().st_mtime_ns + 10_000_000_000
    os.utime(folder / "turn-log.md", ns=(stamp, stamp))
    reply = dict(GOOD, recap="Elbane asked Corwin Ash about the toll. Corwin named 3 gp a wagon.",
                 key_events=["Corwin Ash charges 3 gp a wagon."])
    run(folder, monkeypatch, reply)
    assert "## Session 8 — 2026-11-14" in read(folder, "session-log.md")
    assert summary.records.record_send([{"text": "Next week, the road."}], now=now + 4 * 3600) is None
    assert "\n## Session 9 · " in read(folder, "turn-log.md")


# ── from Sol's review of the stack ──────────────────────────────────────────

def test_a_session_reopened_soon_after_save_and_close_gets_its_own_number(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "campaigns").mkdir(parents=True)
    folder = campaign(root / "campaigns", lines=[])
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("DND_ACTIVE_CAMPAIGN", "test")
    monkeypatch.setattr(summary.records.npc_describe, "kick", lambda: None)
    now = 1_800_000_000.0
    summary.records.record_send([{"text": "The mill wheel turns."}], now=now)
    summary.records.record_send([{"text": "A model switch keeps the session open."}], now=now + 60)
    assert read(folder, "turn-log.md").count("## Session 8 · ") == 1
    (folder / "session-log.md").write_text(LOG + "\n## Session 8 — 2026-11-14\n### Recap\nThe mill wheel turned.\n",
                                           encoding="utf-8")
    summary.records.end_session(folder)  # the portal, after Save & close
    summary.records.record_send([{"text": "An hour later, the road."}], now=now + 3600)
    assert "\n## Session 9 · " in read(folder, "turn-log.md")
    assert summary.session_number(folder) == 9


def test_mid_session_key_events_are_replaced_with_the_whole_sessions(tmp_path, monkeypatch):
    switched = LOG + "\n## Session 8 — 2026-11-14\n### Recap\nDM-SWITCH-RECAP.\n\n### Key Events\n- DM-SWITCH-EVENT\n"
    folder = campaign(tmp_path, log=switched, lines=[])
    turn_log(folder, TURNS_7, TURNS_8)
    run(folder, monkeypatch)
    entry = read(folder, "session-log.md").split("## Session 8")[1]
    assert "DM-SWITCH-EVENT" not in entry and entry.count("### Key Events") == 1
    assert "- Corwin Ash charges 3 gp a wagon." in entry


def test_a_failed_write_puts_back_what_it_already_wrote(tmp_path, monkeypatch):
    folder = campaign(tmp_path, log=LOG_TWO, lines=[])
    turn_log(folder, TURNS_8)
    stub_helper(monkeypatch, dict(GOOD, archived=[ARCHIVED]))
    plan = summary.prepare(folder, summary.display_mark(folder), ("codex", "m"))
    real_replace = summary.os.replace

    def failing(src, dst):
        if Path(dst).name == "state.md":
            raise PermissionError("state.md is locked")
        real_replace(src, dst)

    monkeypatch.setattr(summary.os, "replace", failing)
    with pytest.raises(PermissionError):
        summary.write(folder, plan)
    assert read(folder, "session-log.md") == LOG_TWO and read(folder, "state.md") == STATE
    assert not (folder / "session-log-archive.md").exists()
    assert not [p.name for p in folder.iterdir() if p.name.startswith(".")]
    assert summary.session_number(folder) == 8  # the DM's fallback writes the same session
