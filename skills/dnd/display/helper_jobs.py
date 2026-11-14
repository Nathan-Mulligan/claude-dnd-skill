"""helper_jobs.py — run the background helpers in the display server, outside the DM's sandbox.

Why
  A Codex DM runs in a sandbox, and every process it starts stays inside it. The Codex CLI
  cannot start there, so a helper the DM's scripts started themselves always failed. The
  display server is started by the launcher, never inside the DM, so it runs them instead.

How it runs
  A script the DM runs calls `request(kind)`, which leaves an empty file in
  runtime/helper-jobs/. The display calls `start()` once; from then on it checks every POLL_S
  seconds and runs each requested helper on its own thread, one at a time per kind. A request
  made while that kind is running stays until it has finished, then runs it again, so no
  change is missed.

Rules
  * The display runs the helper code it loaded when it started. The DM can edit the files in
    its sandbox, so a helper is never loaded or started from them again while the display runs.
  * Only the helpers `start()` lists can run, with no arguments. A request file's contents
    are never read.
  * The DM can also place links in the runtime dir, so nothing here writes through one.
  * Each helper keeps its own log (runtime/<kind>.log); nothing is raised.
"""
import os
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import log_line, rt, touch  # noqa: E402

KINDS = ("lore_sync",)
POLL_S = 1.0

_running: "dict[str, threading.Thread]" = {}


def _folder() -> str:
    return rt("helper-jobs")


def request(kind: str) -> None:
    """Ask the display to run this helper. Raises OSError when the request can't be written."""
    if kind not in KINDS:
        raise ValueError(f"unknown helper {kind!r}")
    os.makedirs(_folder(), exist_ok=True)
    touch(os.path.join(_folder(), kind))


def run_due(helpers: dict) -> None:
    """Start every requested helper in `helpers` ({kind: function}) that isn't already running."""
    if os.path.islink(_folder()):
        return
    for kind, run in helpers.items():
        thread = _running.get(kind)
        if thread is not None and thread.is_alive():
            continue
        _running.pop(kind, None)
        try:
            os.unlink(os.path.join(_folder(), kind))  # before the run, so a later request means a later run
        except FileNotFoundError:
            continue
        except OSError as e:
            log_line(rt(f"{kind}.log"), f"could not take the request for {kind}: {type(e).__name__}")
            continue
        thread = threading.Thread(target=run, daemon=True, name=f"dnd-helper-{kind}")
        _running[kind] = thread
        thread.start()


def _helpers() -> dict:
    import lore_sync
    import push_stats  # noqa: F401 — lore_sync imports it on its first run; load it now instead
    return {"lore_sync": lore_sync.run_and_log}


def _loop(helpers: dict) -> None:
    while True:
        try:
            run_due(helpers)
        except Exception:  # noqa: BLE001 — the display must keep serving
            pass
        time.sleep(POLL_S)


def start() -> None:
    """Load the helpers now and run requested ones for as long as this process lives."""
    threading.Thread(target=_loop, args=(_helpers(),), daemon=True, name="dnd-helper-jobs").start()
