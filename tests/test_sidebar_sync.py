"""The sidebar's quests and factions follow state.md, with no push from the DM (#147)."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

DISPLAY = Path(__file__).resolve().parents[1] / "skills/dnd/display"
sys.path.insert(0, str(DISPLAY))
import sidebar_sync  # noqa: E402

STATE = """# Campaign: Test
## World State
- **Faction states:** SECRET-WORLD Road Wardens skim the toll.
## Active Quests
- **Wagon rental** (active): pay the 3 sp rental to its owner at the next market.
- [resolved] Deliver the parcel to the cooperage.
- Collect two crates of iron hoops from the forge on her account; inspect the packing and bring back the receipt.
  - NESTED-NOTE a nested note is not a quest
*(trading quests go here)*
## Faction Standings
- **Road Wardens**: Suspicious — they doubled the toll and won't say why.
- Millers' Compact: Friendly
## Next Hooks
- SECRET-HOOK: a courier carries the auditor's notice.
## Faction Moves
- SECRET-MOVE: the Wardens sent an auditor.
## Live State Flags
**Faction stances**:
- SECRET-FLAG Road Wardens: hostile
## DM Notes (hidden from players)
- SECRET-NOTE
"""


def test_quests_come_from_active_quests_with_name_status_and_detail():
    quests = sidebar_sync.lists(STATE)["quests"]
    assert quests[0] == {"name": "Wagon rental", "status": "active",
                         "detail": "pay the 3 sp rental to its owner at the next market."}
    assert quests[1] == {"name": "Deliver the parcel to the cooperage.", "status": "resolved"}
    long = quests[2]
    assert long["status"] == "active" and len(long["name"]) <= 61 and long["name"].endswith("…")
    assert long["detail"].startswith("Collect two crates") and long["detail"].endswith("the receipt.")
    assert len(quests) == 3 and "NESTED-NOTE" not in repr(quests)


def test_factions_come_from_faction_standings():
    assert sidebar_sync.lists(STATE)["factions"] == [
        {"name": "Road Wardens", "standing": "Suspicious", "detail": "they doubled the toll and won't say why."},
        {"name": "Millers' Compact", "standing": "Friendly"},
    ]


@pytest.mark.parametrize("line, item", [
    ("**Hoops**: [failed] lost them", {"name": "Hoops", "status": "failed", "detail": "lost them"}),
    ("**Hoops** — fetch them", {"name": "Hoops", "status": "active", "detail": "fetch them"}),
    ("Find the smith (done)", {"name": "Find the smith", "status": "resolved"}),
])
def test_quest_line_forms(line, item):
    assert sidebar_sync.quest(line) == item


@pytest.mark.parametrize("line, item", [
    ("**Pale Court** — Hostile; they want the ring", {"name": "Pale Court", "standing": "Hostile",
                                                     "detail": "they want the ring"}),
    ("Road Wardens: Neutral (wary of us)", {"name": "Road Wardens", "standing": "Neutral", "detail": "wary of us"}),
    ("Road Wardens keep the toll", None),
])
def test_faction_line_forms(line, item):
    assert sidebar_sync.faction(line) == item


def test_dm_only_sections_never_reach_the_sidebar():
    assert "SECRET" not in repr(sidebar_sync.lists(STATE))


def test_a_missing_section_leaves_that_list_alone():
    assert sidebar_sync.lists("## Active Quests\n- Find a cargo.\n") == {
        "quests": [{"name": "Find a cargo.", "status": "active"}]}
    assert sidebar_sync.lists("## World State\n- Faction states: x\n") == {}


def test_an_empty_section_clears_its_list():
    assert sidebar_sync.lists("## Active Quests\n*(none yet)*\n\n## Faction Standings\n") == {
        "quests": [], "factions": []}


def test_faction_standings_is_a_party_known_section_for_the_notes_helper():
    import lore_sync
    assert "Suspicious" in lore_sync.public_text(STATE)
    assert "SECRET" not in lore_sync.public_text(STATE)


def test_merge_keeps_a_note_already_shown_for_the_same_name():
    shown = [{"name": "Find a cargo.", "status": "active", "detail": "Helper note."}]
    derived = [{"name": "Find a cargo.", "status": "active"}]
    assert sidebar_sync.merged(shown, derived) is None
    assert sidebar_sync.merged(shown, [{"name": "Find a cargo.", "status": "resolved"}]) == [
        {"name": "Find a cargo.", "status": "resolved", "detail": "Helper note."}]
    assert sidebar_sync.merged(shown, [{"name": "Find a cargo.", "status": "active", "detail": "Own."}]) == [
        {"name": "Find a cargo.", "status": "active", "detail": "Own."}]


def test_read_state_refuses_links_and_non_files(tmp_path):
    real = tmp_path / "real.md"
    real.write_text("## Active Quests\n- A.\n", encoding="utf-8")
    assert sidebar_sync.read_state(real) == real.read_text(encoding="utf-8")
    link = tmp_path / "state.md"
    link.symlink_to(real)
    assert sidebar_sync.read_state(link) is None
    fifo = tmp_path / "fifo.md"
    os.mkfifo(fifo)
    assert sidebar_sync.read_state(fifo) is None
    assert sidebar_sync.read_state(tmp_path / "missing.md") is None


def test_hostile_lines_parse_in_linear_time():
    # Sol review: long whitespace runs made the regexes backtrack for seconds.
    import time
    pad = " " * 200_000
    text = ("## Active Quests\n- " + pad + "\n- x" + pad + "(\n- **A**" + pad + "\n" + "##" + pad + "\n"
            "## Faction Standings\n- Wardens: Neutral" + pad + "x" + pad + "(\n- **B**:" + pad + "\n")
    started = time.monotonic()
    sidebar_sync.lists(text)
    assert time.monotonic() - started < 1


# ── the display keeps the sidebar in step ────────────────────────────────────

@pytest.fixture
def display(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path / ".runtime"))
    monkeypatch.setenv("DND_LORE_SYNC", "0")
    monkeypatch.syspath_prepend(str(DISPLAY))
    import runtime_paths
    (tmp_path / ".runtime").mkdir(exist_ok=True)
    for name in ("Fenmoor", "Clockwork"):
        (tmp_path / "campaigns" / name).mkdir(parents=True)
    monkeypatch.setattr(runtime_paths, "RT", str(tmp_path / ".runtime"))
    spec = importlib.util.spec_from_file_location("_sidebar_sync_test", DISPLAY / "dnd-display-app.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.app.testing = True
    sent = []
    module._broadcast = sent.append
    kicks = []
    import lore_sync
    monkeypatch.setattr(lore_sync, "kick", lambda: kicks.append(1))
    client = module.app.test_client()
    assert client.post("/clear", json={}).status_code == 204
    assert client.post("/stats", json={"campaign": "Fenmoor"}).status_code == 204
    sent.clear()
    return module, client, tmp_path / "campaigns" / "Fenmoor" / "state.md", sent, kicks


def test_a_quest_change_in_state_md_reaches_the_sidebar_without_a_push(display):
    module, _, state, sent, kicks = display
    state.write_text("## Active Quests\n- **Hoops** (active): fetch them.\n", encoding="utf-8")
    assert module._sync_sidebar_from_state() is True
    assert module._current_stats["quests"] == [{"name": "Hoops", "status": "active", "detail": "fetch them."}]
    assert sent[-1]["stats"]["quests"][0]["name"] == "Hoops"
    assert kicks == [1]
    assert module._sync_sidebar_from_state() is False  # unchanged: no broadcast, no helper run
    assert len(sent) == 1 and kicks == [1]
    state.write_text("## Active Quests\n- **Hoops** (resolved): fetched.\n", encoding="utf-8")
    assert module._sync_sidebar_from_state() is True
    assert module._current_stats["quests"][0]["status"] == "resolved"


def test_a_dm_push_that_disagrees_with_state_md_is_put_right(display):
    module, client, state, _, _ = display
    state.write_text("## Active Quests\n- Find a cargo.\n## Faction Standings\n- Wardens: Neutral\n",
                     encoding="utf-8")
    client.post("/stats", json={"quests": [{"name": "Old quest", "status": "active"}]})
    assert module._sync_sidebar_from_state() is True
    assert [q["name"] for q in module._current_stats["quests"]] == ["Find a cargo."]
    assert module._current_stats["factions"] == [{"name": "Wardens", "standing": "Neutral"}]


def test_without_the_sections_the_dm_pushed_lists_stand(display):
    module, client, state, _, _ = display
    state.write_text("## Current Situation\n- In town.\n", encoding="utf-8")
    client.post("/stats", json={"quests": [{"name": "Pushed", "status": "active"}]})
    assert module._sync_sidebar_from_state() is False
    assert module._current_stats["quests"] == [{"name": "Pushed", "status": "active"}]


def test_another_campaigns_state_never_reaches_the_sidebar(display):
    module, client, state, _, _ = display
    other = state.parents[1] / "Clockwork" / "state.md"
    other.write_text("## Active Quests\n- Clockwork quest.\n", encoding="utf-8")
    assert module._sync_sidebar_from_state() is False
    assert "quests" not in module._current_stats
    client.post("/clear", json={})  # stats belong to no campaign until one is selected
    state.write_text("## Active Quests\n- Fenmoor quest.\n", encoding="utf-8")
    assert module._sync_sidebar_from_state() is False


def test_a_fifo_campaign_marker_never_hangs_the_sync(display):
    module, _, state, _, _ = display
    state.write_text("## Active Quests\n- Find a cargo.\n", encoding="utf-8")
    marker = Path(module.CAMP_FILE)
    marker.unlink()
    os.mkfifo(marker)
    assert module._sync_sidebar_from_state() is False


def test_a_linked_state_md_is_not_followed(display, tmp_path):
    module, _, state, _, _ = display
    outside = tmp_path / "outside.md"
    outside.write_text("## Active Quests\n- SECRET-OUTSIDE\n", encoding="utf-8")
    state.symlink_to(outside)
    assert module._sync_sidebar_from_state() is False
    assert "quests" not in module._current_stats
