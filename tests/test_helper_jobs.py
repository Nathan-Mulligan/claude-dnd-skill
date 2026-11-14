"""Background helpers run outside the DM's sandbox: scripts the DM runs only leave a request,
and the display server runs the helper it loaded at start."""
import importlib.util
import os
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DISPLAY = REPO / "skills/dnd/display"
sys.path.insert(0, str(DISPLAY))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, DISPLAY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper_jobs = _load("helper_jobs")
lore_sync = _load("lore_sync")
runtime_paths = _load("runtime_paths")


class Helper:
    """A helper that records each run and holds until released."""

    def __init__(self):
        self.runs, self.release = 0, threading.Event()

    def __call__(self):
        self.runs += 1
        self.release.wait(5)

    def finish(self):
        self.release.set()
        for thread in list(helper_jobs._running.values()):
            thread.join(5)
        self.release.clear()


@pytest.fixture
def jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(helper_jobs, "rt", lambda name: str(tmp_path / name))
    monkeypatch.setattr(helper_jobs, "_running", {})
    helper = Helper()
    yield tmp_path, helper
    helper.release.set()


def test_request_leaves_a_file_and_runs_nothing(jobs):
    tmp, helper = jobs
    helper_jobs.request("lore_sync")
    helper_jobs.request("lore_sync")  # asking twice is one request
    assert [p.name for p in (tmp / "helper-jobs").iterdir()] == ["lore_sync"]
    assert helper.runs == 0


def test_unknown_helper_cannot_be_requested(jobs):
    with pytest.raises(ValueError):
        helper_jobs.request("../../bin/sh")


def test_runner_runs_a_requested_helper_once(jobs):
    tmp, helper = jobs
    helpers = {"lore_sync": helper}
    helper_jobs.run_due(helpers)
    assert helper.runs == 0
    helper_jobs.request("lore_sync")
    helper_jobs.run_due(helpers)
    helper_jobs.run_due(helpers)
    helper.finish()
    assert helper.runs == 1
    assert not (tmp / "helper-jobs" / "lore_sync").exists()


def test_request_while_running_waits_then_runs_again(jobs):
    _, helper = jobs
    helpers = {"lore_sync": helper}
    helper_jobs.request("lore_sync")
    helper_jobs.run_due(helpers)
    helper_jobs.request("lore_sync")  # the DM pushes again while the first pass is busy
    helper_jobs.run_due(helpers)
    assert helper.runs == 1
    helper.finish()
    helper_jobs.run_due(helpers)
    helper.finish()
    assert helper.runs == 2
    helper_jobs.run_due(helpers)
    helper.finish()
    assert helper.runs == 2  # nothing new was asked for


def test_only_listed_helpers_run_whatever_the_folder_holds(jobs):
    tmp, helper = jobs
    folder = tmp / "helper-jobs"
    folder.mkdir()
    (folder / "evil").write_text("rm -rf ~", encoding="utf-8")
    (folder / "lore_sync").write_text("--anything /etc/passwd", encoding="utf-8")
    helper_jobs.run_due({"lore_sync": helper})
    helper.finish()
    assert helper.runs == 1
    assert (folder / "evil").exists()


def test_display_runs_the_helper_code_it_loaded_at_start():
    helpers = helper_jobs._helpers()
    assert set(helpers) == set(helper_jobs.KINDS)
    run = helpers["lore_sync"]
    assert run.__name__ == "run_and_log" and Path(run.__code__.co_filename).name == "lore_sync.py"
    assert "push_stats" in sys.modules  # lore_sync's lazy import is already done


def test_display_server_starts_the_runner():
    source = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
    main = source[source.index('if __name__ == "__main__":'):]
    assert "helper_jobs.start()" in main


# ── nothing outside the runtime dir is written through a link the DM placed ──

def test_a_linked_request_folder_is_ignored(jobs, tmp_path):
    tmp, helper = jobs
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "lore_sync").write_text("keep me", encoding="utf-8")
    (tmp / "helper-jobs").symlink_to(outside)
    helper_jobs.run_due({"lore_sync": helper})
    assert helper.runs == 0
    assert (outside / "lore_sync").read_text(encoding="utf-8") == "keep me"


def test_a_request_is_never_written_through_a_link(jobs, tmp_path):
    tmp, _ = jobs
    (tmp / "helper-jobs").mkdir()
    target = tmp_path / "elsewhere"
    (tmp / "helper-jobs" / "lore_sync").symlink_to(target)
    with pytest.raises(OSError):
        helper_jobs.request("lore_sync")
    assert not target.exists()


def test_log_line_never_writes_through_a_link(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text("model = 'x'\n", encoding="utf-8")
    (tmp_path / "soft.log").symlink_to(target)
    os.link(target, tmp_path / "hard.log")
    runtime_paths.log_line(str(tmp_path / "soft.log"), "injected")
    runtime_paths.log_line(str(tmp_path / "hard.log"), "injected")
    assert target.read_text(encoding="utf-8") == "model = 'x'\n"
    runtime_paths.log_line(str(tmp_path / "plain.log"), "written")
    assert (tmp_path / "plain.log").read_text(encoding="utf-8").endswith(" written\n")


def test_busy_flag_is_never_written_through_a_link(monkeypatch, tmp_path):
    monkeypatch.setattr(lore_sync, "rt", lambda name: str(tmp_path / name))
    (tmp_path / "lore_sync.lock").touch()
    target = tmp_path / "elsewhere"
    (tmp_path / "lore_sync.pending").symlink_to(target)
    assert lore_sync.run(settle_s=0) == "busy"
    assert not target.exists()


# ── lore_sync asks; the display runs ─────────────────────────────────────────

@pytest.fixture
def lore_env(monkeypatch, tmp_path):
    monkeypatch.setattr(lore_sync, "rt", lambda name: str(tmp_path / name))
    monkeypatch.setattr(helper_jobs, "rt", lambda name: str(tmp_path / name))
    monkeypatch.setitem(sys.modules, "helper_jobs", helper_jobs)
    monkeypatch.delenv("DND_LORE_SYNC", raising=False)
    monkeypatch.delenv("DND_LORE_MODEL", raising=False)
    monkeypatch.setattr(lore_sync.subprocess, "Popen", lambda *a, **k: pytest.fail("the DM side must never start a helper"))
    return tmp_path


def test_kick_leaves_a_request_for_the_display(lore_env):
    lore_sync.kick()
    assert (lore_env / "helper-jobs" / "lore_sync").exists()


def test_kick_asks_nothing_when_the_helper_is_off(lore_env):
    lore_sync.save_helper("off")
    lore_sync.kick()
    assert not (lore_env / "helper-jobs").exists()


def test_kick_never_raises_when_the_request_cannot_be_written(lore_env):
    (lore_env / "helper-jobs").write_text("a file, not a folder", encoding="utf-8")
    lore_sync.kick()
    assert "kick failed" in (lore_env / "lore_sync.log").read_text(encoding="utf-8")


def test_run_and_log_records_a_failed_run(monkeypatch, tmp_path):
    monkeypatch.setattr(lore_sync, "rt", lambda name: str(tmp_path / name))

    def broken():
        raise OSError("display down")
    monkeypatch.setattr(lore_sync, "run", broken)
    lore_sync.run_and_log()
    assert "run failed: OSError: display down" in (tmp_path / "lore_sync.log").read_text(encoding="utf-8")
