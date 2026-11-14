"""Background helpers run outside the DM's sandbox: scripts the DM runs only leave a request,
and the display server starts the helper."""
import importlib.util
import sys
import time
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


class FakeProc:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


@pytest.fixture
def jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(helper_jobs, "rt", lambda name: str(tmp_path / name))
    monkeypatch.setattr(helper_jobs, "_running", {})
    started = []

    def popen(cmd, **kwargs):
        proc = FakeProc()
        started.append((cmd, kwargs, proc))
        return proc
    monkeypatch.setattr(helper_jobs.subprocess, "Popen", popen)
    return tmp_path, started


def test_request_leaves_a_file_and_starts_nothing(jobs):
    tmp, started = jobs
    helper_jobs.request("lore_sync")
    helper_jobs.request("lore_sync")  # asking twice is one request
    assert [p.name for p in (tmp / "helper-jobs").iterdir()] == ["lore_sync"]
    assert started == []


def test_unknown_helper_cannot_be_requested(jobs):
    with pytest.raises(ValueError):
        helper_jobs.request("../../bin/sh")


def test_runner_starts_a_requested_helper_once(jobs):
    tmp, started = jobs
    helper_jobs.run_due()
    assert started == []
    helper_jobs.request("lore_sync")
    helper_jobs.run_due()
    helper_jobs.run_due()
    assert len(started) == 1
    cmd, kwargs, _ = started[0]
    assert cmd == [sys.executable, str(DISPLAY / "lore_sync.py"), "--run"]
    assert kwargs["start_new_session"] is True and kwargs["stdin"] is helper_jobs.subprocess.DEVNULL
    assert not (tmp / "helper-jobs" / "lore_sync").exists()


def test_request_while_running_waits_then_runs_again(jobs):
    _, started = jobs
    helper_jobs.request("lore_sync")
    helper_jobs.run_due()
    helper_jobs.request("lore_sync")  # the DM pushes again while the first pass is busy
    helper_jobs.run_due()
    assert len(started) == 1
    started[0][2].code = 0
    helper_jobs.run_due()
    assert len(started) == 2
    started[1][2].code = 1
    helper_jobs.run_due()
    assert len(started) == 2  # nothing new was asked for


def test_only_listed_helpers_run_whatever_the_folder_holds(jobs):
    tmp, started = jobs
    folder = tmp / "helper-jobs"
    folder.mkdir()
    (folder / "evil").write_text("rm -rf ~", encoding="utf-8")
    (folder / "lore_sync").write_text("--anything /etc/passwd", encoding="utf-8")
    helper_jobs.run_due()
    assert [cmd for cmd, _, _ in started] == [[sys.executable, str(DISPLAY / "lore_sync.py"), "--run"]]


def test_a_helper_that_cannot_start_is_logged_not_raised(jobs, monkeypatch):
    tmp, _ = jobs

    def broken(*a, **k):
        raise OSError("no python")
    monkeypatch.setattr(helper_jobs.subprocess, "Popen", broken)
    helper_jobs.request("lore_sync")
    helper_jobs.run_due()
    assert "could not start lore_sync: OSError: no python" in (tmp / "lore_sync.log").read_text(encoding="utf-8")


def test_runner_really_starts_the_helper_script(monkeypatch, tmp_path):
    script = tmp_path / "fake_helper.py"
    out = tmp_path / "ran.txt"
    script.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(out)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8")
    monkeypatch.setattr(helper_jobs, "rt", lambda name: str(tmp_path / name))
    monkeypatch.setattr(helper_jobs, "_running", {})
    monkeypatch.setitem(helper_jobs.HELPERS, "lore_sync", str(script))
    helper_jobs.request("lore_sync")
    helper_jobs.run_due()
    deadline = time.monotonic() + 10
    while not out.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert out.read_text(encoding="utf-8") == "--run"


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


def test_display_server_runs_the_helper_jobs():
    source = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")
    main = source[source.index('if __name__ == "__main__":'):]
    assert "helper_jobs.start()" in main
