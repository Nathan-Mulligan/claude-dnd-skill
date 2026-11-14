"""The notes helper writes the Save & close summary: only what happened, nothing lost."""
import importlib.util
import json
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
