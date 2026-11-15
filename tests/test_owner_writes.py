"""Writes a sandboxed DM can't make are made by the display on its request, for its own campaign only (#138)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DISPLAY = REPO / "skills/dnd/display"
SCRIPTS = REPO / "skills/dnd/scripts"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(SCRIPTS))

posix_only = pytest.mark.skipif(os.name == "nt", reason="links and read-only folders are POSIX-only here")
SHEET = "# Corin Ashby\n\n## Spells\n- **Toll the Dead**\n"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    monkeypatch.setenv("DND_RUNTIME_DIR", str(tmp_path / ".runtime"))
    for name in ("Fenmoor", "Clockwork"):
        (tmp_path / "campaigns" / name / "characters").mkdir(parents=True)
        (tmp_path / "campaigns" / name / "state.md").write_text(f"# {name}\n", encoding="utf-8")
    (tmp_path / "campaigns/Fenmoor/characters/Corin Ashby.md").write_text(SHEET, encoding="utf-8")
    (tmp_path / "campaigns/Clockwork/characters/Pip Rowan.md").write_text("# Pip Rowan\n", encoding="utf-8")
    (tmp_path / ".runtime").mkdir()
    return tmp_path


@pytest.fixture
def jobs(root, monkeypatch):
    helper_jobs = load("_writes_helper_jobs", DISPLAY / "helper_jobs.py")
    monkeypatch.setattr(helper_jobs, "rt", lambda name: str(root / ".runtime" / name))
    monkeypatch.setitem(sys.modules, "helper_jobs", helper_jobs)
    return helper_jobs


@pytest.fixture
def writes(root):
    return load("_owner_writes", DISPLAY / "owner_writes.py")


def requests(root):
    folder = root / ".runtime" / "helper-jobs" / "writes"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []


def log(root):
    path = root / ".runtime" / "owner-writes.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ── The request mechanism ──────────────────────────────────────────────────────

def test_a_write_request_is_a_small_json_file(jobs, root):
    jobs.request_write("roster", {"campaign": "Fenmoor"})
    assert requests(root) == [{"kind": "roster", "payload": {"campaign": "Fenmoor"}}]
    with pytest.raises(ValueError):
        jobs.request_write("shell", {})
    with pytest.raises(ValueError):
        jobs.request_write("names", {"name": "x" * 20000})


@posix_only  # without dir_fd the display doesn't read requests (Windows)
def test_the_display_runs_each_request_once_with_the_campaign_it_shows(jobs, root):
    seen = []
    jobs.request_write("roster", {"campaign": "Fenmoor"})
    jobs.request_write("names", {"op": "add"})
    jobs.run_writes({"roster": lambda payload, active: seen.append(("roster", payload, active)) or "ok",
                     "names": lambda payload, active: seen.append(("names", payload, active)) or "ok"},
                    lambda: "Fenmoor")
    assert seen == [("roster", {"campaign": "Fenmoor"}, "Fenmoor"), ("names", {"op": "add"}, "Fenmoor")]
    assert requests(root) == []
    jobs.run_writes({"roster": lambda *a: pytest.fail("ran twice")}, lambda: "Fenmoor")


@posix_only  # without dir_fd the display doesn't read requests (Windows)
def test_a_refused_or_unknown_request_is_logged_and_the_rest_still_run(jobs, root):
    folder = root / ".runtime" / "helper-jobs" / "writes"
    jobs.request_write("roster", {"campaign": "Clockwork"})
    (folder / "0-bad.json").write_text("not json", encoding="utf-8")
    (folder / "1-odd.json").write_text(json.dumps({"kind": "shell", "payload": {}}), encoding="utf-8")
    ran = []

    def roster(payload, active):
        raise ValueError("Clockwork is not the campaign the display is showing")

    jobs.run_writes({"roster": roster, "names": lambda *a: ran.append(a)}, lambda: "Fenmoor")
    assert list(folder.iterdir()) == []
    assert "roster refused: Clockwork is not" in log(root) and log(root).count("dropped") == 2


@posix_only
def test_a_linked_request_is_deleted_unread_and_its_target_kept(jobs, root):
    target = root / "outside.json"
    target.write_text(json.dumps({"kind": "roster", "payload": {"campaign": "Fenmoor"}}), encoding="utf-8")
    folder = root / ".runtime" / "helper-jobs" / "writes"
    folder.mkdir(parents=True)
    (folder / "1-link.json").symlink_to(target)
    jobs.run_writes({"roster": lambda *a: pytest.fail("read through a link")}, lambda: "Fenmoor")
    assert target.exists() and not (folder / "1-link.json").exists()


@posix_only
def test_a_linked_writes_folder_is_never_read_or_emptied(jobs, root):
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "1-keep.json").write_text(json.dumps({"kind": "roster", "payload": {}}), encoding="utf-8")
    (root / ".runtime" / "helper-jobs").mkdir()
    (root / ".runtime" / "helper-jobs" / "writes").symlink_to(elsewhere, target_is_directory=True)
    jobs.run_writes({"roster": lambda *a: pytest.fail("read a linked folder")}, lambda: "Fenmoor")
    assert (elsewhere / "1-keep.json").exists()


def test_the_display_starts_the_writer_with_its_campaign():
    source = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
    main = source[source.index('if __name__ == "__main__":'):]
    assert "helper_jobs.start(_active_campaign_name)" in main


# ── Roster mirror ──────────────────────────────────────────────────────────────

def test_roster_copies_the_shown_campaigns_sheets(writes, root):
    assert "copied 1" in writes.mirror_roster("Fenmoor", "Fenmoor")
    assert (root / "characters/Corin Ashby.md").read_text(encoding="utf-8") == SHEET
    assert not (root / "characters/Pip Rowan.md").exists()


@pytest.mark.parametrize("campaign", ["Clockwork", "../Fenmoor", "Fenmoor/characters", "", None, 7])
def test_roster_refuses_any_other_campaign(writes, root, campaign):
    with pytest.raises(ValueError):
        writes.mirror_roster(campaign, "Fenmoor")
    assert not (root / "characters").exists()


def test_a_renamed_pcs_old_roster_copy_is_set_aside_not_deleted(writes, root):
    roster = root / "characters"
    roster.mkdir()
    (roster / "corin_old.md").write_text("# Corin Old\n", encoding="utf-8")
    assert "moved corin_old.md" in writes.roster_request({"campaign": "Fenmoor", "replaces": "corin_old.md"}, "Fenmoor")
    assert not (roster / "corin_old.md").exists()
    [kept] = (roster / ".retired").iterdir()
    assert kept.read_text(encoding="utf-8") == "# Corin Old\n"
    for bad in ("Corin Ashby.md", "../state.md", ".retired", "x.txt", 5):
        with pytest.raises(ValueError):
            writes.roster_request({"campaign": "Fenmoor", "replaces": bad}, "Fenmoor")
    assert (roster / "Corin Ashby.md").exists()


@posix_only
def test_roster_never_copies_through_a_link(writes, root):
    secret = root / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    characters = root / "campaigns/Fenmoor/characters"
    (characters / "Stolen.md").symlink_to(secret)
    (characters / "notes.txt").write_text("not a sheet", encoding="utf-8")
    (characters / ".hidden.md").write_text("hidden", encoding="utf-8")
    (characters / "Huge.md").write_text("x" * (writes.SHEET_MAX_BYTES + 1), encoding="utf-8")
    writes.mirror_roster("Fenmoor", "Fenmoor")
    assert sorted(p.name for p in (root / "characters").iterdir()) == ["Corin Ashby.md"]


@posix_only
def test_roster_refuses_a_linked_campaign_or_characters_folder(writes, root):
    (root / "campaigns/Linked").symlink_to(root / "campaigns/Clockwork", target_is_directory=True)
    with pytest.raises(ValueError):
        writes.mirror_roster("Linked", "Linked")
    characters = root / "campaigns/Fenmoor/characters"
    characters.rename(root / "moved")
    characters.symlink_to(root / "campaigns/Clockwork/characters", target_is_directory=True)
    assert "copied 0" in writes.mirror_roster("Fenmoor", "Fenmoor")


@posix_only
def test_roster_by_hand_copies_itself_and_a_sandboxed_dm_asks_the_display(writes, jobs, root):
    script = DISPLAY / "owner_writes.py"
    env = dict(os.environ, DND_CAMPAIGN_ROOT=str(root), DND_RUNTIME_DIR=str(root / ".runtime"))
    done = subprocess.run([sys.executable, str(script), "roster", "--campaign", "Fenmoor"], env=env,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (root / "characters/Corin Ashby.md").exists()
    (root / "characters/Corin Ashby.md").unlink()
    (root / "characters").chmod(0o500)
    try:
        asked = subprocess.run([sys.executable, str(script), "roster", "--campaign", "Fenmoor"], env=env,
                               capture_output=True, text=True)
    finally:
        (root / "characters").chmod(0o700)
    assert asked.returncode == 0, asked.stderr
    assert requests(root) == [{"kind": "roster", "payload": {"campaign": "Fenmoor"}}]
    jobs.run_writes(writes.writers(), lambda: "Fenmoor")
    assert (root / "characters/Corin Ashby.md").read_text(encoding="utf-8") == SHEET


# ── Name registry ──────────────────────────────────────────────────────────────

def registry(root):
    path = root / ".name_registry.json"
    return json.loads(path.read_text(encoding="utf-8"))["entries"] if path.exists() else {}


def test_names_are_added_and_retired_for_the_shown_campaign(writes, root):
    writes.record_name({"op": "add", "name": "Mira Vell", "type": "npc", "campaign": "Fenmoor", "session": 3},
                       "Fenmoor")
    assert registry(root)["mira_vell"]["currently_active_in"] == ["Fenmoor"]
    writes.record_name({"op": "retire", "name": "Mira Vell", "campaign": "Fenmoor", "replaced_by": "Mira Holt"},
                       "Fenmoor")
    entry = registry(root)["mira_vell"]
    assert entry["currently_active_in"] == [] and entry["retired_from"][0]["replaced_by"] == "mira_holt"


@pytest.mark.parametrize("payload", [
    {"op": "add", "name": "Mira", "type": "npc", "campaign": "Clockwork", "session": 1},
    {"op": "add", "name": "Mira", "type": "god", "campaign": "Fenmoor", "session": 1},
    {"op": "add", "name": "Mira\nVell", "type": "npc", "campaign": "Fenmoor", "session": 1},
    {"op": "add", "name": "M" * 101, "type": "npc", "campaign": "Fenmoor", "session": 1},
    {"op": "add", "name": "Mira", "type": "npc", "campaign": "Fenmoor", "session": "1"},
    {"op": "add", "name": "Mira", "type": "npc", "campaign": "Fenmoor", "session": 1, "path": "/etc"},
    {"op": "rebuild", "campaign": "Fenmoor"},
    ["add"],
])
def test_a_bad_name_request_changes_nothing(writes, root, payload):
    with pytest.raises(ValueError):
        writes.record_name(payload, "Fenmoor")
    assert registry(root) == {}


def test_a_name_that_cannot_be_saved_leaves_the_registry_intact(writes, root):
    writes.record_name({"op": "add", "name": "Mira Vell", "type": "npc", "campaign": "Fenmoor", "session": 1},
                       "Fenmoor")
    before = (root / ".name_registry.json").read_bytes()
    lone = json.loads('"Mira \\ud800"')
    with pytest.raises(ValueError):
        writes.record_name({"op": "add", "name": lone, "type": "npc", "campaign": "Fenmoor", "session": 1}, "Fenmoor")
    import name_registry
    with pytest.raises(UnicodeEncodeError):
        name_registry.add(lone, "npc", "Fenmoor", 1)
    assert (root / ".name_registry.json").read_bytes() == before


@posix_only
def test_a_sandboxed_name_registry_add_asks_the_display(writes, jobs, root):
    import name_registry
    root.chmod(0o500)
    try:
        entry = name_registry.add("Mira Vell", "npc", "Fenmoor", 2)
    finally:
        root.chmod(0o700)
    assert entry["name"] == "Mira Vell" and registry(root) == {}
    assert requests(root) == [{"kind": "names", "payload": {"op": "add", "name": "Mira Vell", "type": "npc",
                                                             "campaign": "Fenmoor", "session": 2}}]
    jobs.run_writes(writes.writers(), lambda: "Fenmoor")
    assert registry(root)["mira_vell"]["first_session"] == 2


@posix_only
def test_the_display_never_asks_itself_again(writes, root):
    root.chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            writes.record_name({"op": "add", "name": "Mira", "type": "npc", "campaign": "Fenmoor", "session": 1},
                               "Fenmoor")
    finally:
        root.chmod(0o700)
    assert requests(root) == []


# ── Supplemental data ──────────────────────────────────────────────────────────

@pytest.fixture
def supplemental(tmp_path, monkeypatch):
    import build_supplemental
    data = tmp_path / "data"
    data.mkdir()
    (data / "dnd5e_srd.json").write_text(json.dumps({"spells": [], "features": []}), encoding="utf-8")
    for name in ("DATA_FILE", "SUPPLEMENTAL_FILE"):  # fetch_missing repoints them; restore them afterwards
        monkeypatch.setattr(build_supplemental, name, getattr(build_supplemental, name))
    monkeypatch.setattr(build_supplemental, "SKILLS_DIR", str(tmp_path))
    monkeypatch.setattr(build_supplemental, "FETCH_DELAY", 0)
    monkeypatch.setattr(build_supplemental, "_build_entry",
                        lambda name, cat: {"name": name, "source": "stub", "description": "stub"})
    monkeypatch.setattr(build_supplemental, "_load_srd_names", lambda: set())
    return build_supplemental, data / "dnd5e_supplemental.json"


def test_the_display_fetches_the_shown_campaigns_supplemental(writes, root, supplemental):
    _, saved = supplemental
    assert "added 1" in writes.fetch_supplemental({"campaign": "Fenmoor", "ruleset": "2014"}, "Fenmoor")
    assert [s["name"] for s in json.loads(saved.read_text(encoding="utf-8"))["spells"]] == ["Toll the Dead"]
    for payload in ({"campaign": "Clockwork", "ruleset": "2014"}, {"campaign": "Fenmoor", "ruleset": "1999"},
                    {"campaign": "Fenmoor"}, {"campaign": "Fenmoor", "ruleset": "2014", "file": "/etc/passwd"}):
        with pytest.raises(ValueError):
            writes.fetch_supplemental(payload, "Fenmoor")


def test_the_display_parses_the_sheets_it_already_read(writes, root, supplemental, monkeypatch):
    build, _ = supplemental
    monkeypatch.setattr(build, "_extract_names_from_character", lambda path: pytest.fail("reopened a sheet"))
    assert "added 1" in writes.fetch_supplemental({"campaign": "Fenmoor", "ruleset": "2014"}, "Fenmoor")


def test_a_sandboxed_supplemental_build_asks_the_display(jobs, root, supplemental, monkeypatch):
    build, saved = supplemental

    def refuse(data):
        raise PermissionError("operation not permitted")

    monkeypatch.setattr(build, "_save_supplemental", refuse)
    monkeypatch.setattr(build, "CAMPAIGNS_DIR", str(root / "campaigns"))
    sheet = root / "campaigns/Fenmoor/characters/Corin Ashby.md"
    monkeypatch.setattr(sys, "argv", ["build_supplemental.py", "--character", str(sheet)])
    build.main()
    assert requests(root) == [{"kind": "supplemental", "payload": {"campaign": "Fenmoor", "ruleset": "2014"}}]
    assert not saved.exists()


# ── NPC rename backup ──────────────────────────────────────────────────────────

@posix_only
def test_a_rename_backs_up_inside_its_campaign(root):
    campaign = root / "campaigns/Fenmoor"
    (campaign / "npcs.md").write_text("| Mira Vell | innkeeper |\n", encoding="utf-8")
    (root / "campaigns").chmod(0o500)
    try:
        env = dict(os.environ, DND_CAMPAIGN_ROOT=str(root), DND_RUNTIME_DIR=str(root / ".runtime"))
        for new in ("Mira Holt", "Mira Dunn"):
            old = "Mira Vell" if new == "Mira Holt" else "Mira Holt"
            done = subprocess.run([sys.executable, str(SCRIPTS / "npc_rename.py"), "--campaign", "Fenmoor",
                                   "--old", old, "--new", new, "--yes"], env=env, capture_output=True, text=True)
            assert done.returncode == 0, done.stderr
    finally:
        (root / "campaigns").chmod(0o700)
    assert (campaign / "npcs.md").read_text(encoding="utf-8").startswith("| Mira Dunn |")
    first, second = sorted((campaign / ".rename-backups").iterdir())
    assert (first / "npcs.md").read_text(encoding="utf-8").startswith("| Mira Vell |")
    assert (second / "npcs.md").read_text(encoding="utf-8").startswith("| Mira Holt |")
    assert not (second / ".rename-backups").exists()
    assert sorted(p.name for p in (root / "campaigns").iterdir()) == ["Clockwork", "Fenmoor"]
