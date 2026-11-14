"""helper_jobs.py — start the background helpers from the display server, outside the DM's sandbox.

Why
  A Codex DM runs in a sandbox, and every process it starts stays inside it. The Codex CLI
  cannot start there, so a helper the DM's scripts started themselves always failed. The
  display server is started by the launcher, never inside the DM, so it starts them instead.

How it runs
  A script the DM runs calls `request(kind)`, which leaves an empty file in
  runtime/helper-jobs/. The display calls `run_due()` every POLL_S seconds and starts each
  requested helper as `<script> --run`, one at a time per kind. A request made while that kind
  is running stays until it has finished, then starts it again, so no change is missed.

Rules
  * Only the scripts in HELPERS can start, always with `--run` alone. A request file's
    contents are never read, so the DM cannot choose what runs or pass it anything.
  * Each helper reads its own work from the runtime dir and keeps its own log
    (runtime/<kind>.log). A helper that cannot start is logged there; nothing is raised.
"""
import os
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt  # noqa: E402

HELPERS = {"lore_sync": os.path.join(_HERE, "lore_sync.py")}
POLL_S = 1.0

_running: "dict[str, subprocess.Popen]" = {}


def _request_path(kind: str) -> str:
    return os.path.join(rt("helper-jobs"), kind)


def request(kind: str) -> None:
    """Ask the display to run this helper. Raises OSError when the runtime dir can't be written."""
    if kind not in HELPERS:
        raise ValueError(f"unknown helper {kind!r}")
    os.makedirs(rt("helper-jobs"), exist_ok=True)
    with open(_request_path(kind), "a", encoding="utf-8"):
        pass


def _log(kind: str, msg: str) -> None:
    try:
        with open(rt(f"{kind}.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def run_due() -> None:
    """Start every requested helper that isn't already running."""
    for kind, script in HELPERS.items():
        proc = _running.get(kind)
        if proc is not None and proc.poll() is None:
            continue
        _running.pop(kind, None)
        try:
            os.unlink(_request_path(kind))  # before the start, so a later request means a later run
        except FileNotFoundError:
            continue
        except OSError as e:
            _log(kind, f"could not take the request for {kind}: {type(e).__name__}: {e}")
            continue
        try:
            _running[kind] = subprocess.Popen(
                [sys.executable, script, "--run"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True)
        except Exception as e:  # noqa: BLE001 — the display must keep serving
            _log(kind, f"could not start {kind}: {type(e).__name__}: {e}")


def _loop() -> None:
    while True:
        try:
            run_due()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(POLL_S)


def start() -> None:
    """Run requested helpers in the background for as long as this process lives."""
    threading.Thread(target=_loop, daemon=True, name="dnd-helper-jobs").start()
