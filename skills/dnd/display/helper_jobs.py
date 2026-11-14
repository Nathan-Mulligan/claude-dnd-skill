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

Writes on request (owner_writes.py)
  A script the DM runs can also call `request_write(kind, payload)` when its sandbox refuses a
  write. That leaves a small JSON file in runtime/helper-jobs/writes/; the display reads it once,
  deletes it, and hands the payload and the campaign it is showing to the writer for that kind,
  which checks it strictly before writing anything.

Rules
  * The display runs the helper code it loaded when it started. The DM can edit the files in
    its sandbox, so a helper is never loaded or started from them again while the display runs.
  * Only the helpers `start()` lists can run, with no arguments. A helper request file's
    contents are never read; a write request's contents are only ever data for its writer.
  * The DM can also place links in the runtime dir, so nothing here writes through one.
  * Each helper keeps its own log (runtime/<kind>.log); nothing is raised.
"""
import json
import os
import secrets
import stat
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import _DIR_FD, _open_folder, log_line, rt, touch, write_file  # noqa: E402

KINDS = ("lore_sync", "npc_describe")
WRITE_KINDS = ("roster", "names", "supplemental")
WRITE_MAX_BYTES = 16384
WRITES_PER_PASS = 20
POLL_S = 1.0
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)

_running: "dict[str, threading.Thread]" = {}


def _folder() -> str:
    return rt("helper-jobs")


def request(kind: str) -> None:
    """Ask the display to run this helper. Raises OSError when the request can't be written."""
    if kind not in KINDS:
        raise ValueError(f"unknown helper {kind!r}")
    os.makedirs(_folder(), exist_ok=True)
    touch(os.path.join(_folder(), kind))


def _writes_folder() -> str:
    return rt(os.path.join("helper-jobs", "writes"))


def request_write(kind: str, payload: dict) -> None:
    """Ask the display to make a write the DM's sandbox refuses (owner_writes.py).

    Raises ValueError for an unknown kind or an oversized payload, OSError when it can't be asked.
    """
    if kind not in WRITE_KINDS:
        raise ValueError(f"unknown write {kind!r}")
    data = json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False)
    if len(data.encode("utf-8")) > WRITE_MAX_BYTES:
        raise ValueError("write request is too large")
    name = f"{time.time_ns()}-{secrets.token_hex(4)}.json"
    write_file(os.path.join(_writes_folder(), name), data, root=rt(""), mode=0o600)


def _take_request(folder_fd: int, name: str) -> "dict | None":
    """Read one request file and delete it. A link, FIFO or oversized file is deleted unread."""
    data = None
    try:
        fd = os.open(name, os.O_RDONLY | _NOFOLLOW | _NONBLOCK, dir_fd=folder_fd)
    except OSError:
        fd = None
    if fd is not None:
        try:
            info = os.fstat(fd)
            if stat.S_ISREG(info.st_mode) and info.st_size <= WRITE_MAX_BYTES:
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    data = json.loads(stream.read(WRITE_MAX_BYTES).decode("utf-8"))
        except (OSError, ValueError):
            data = None
        finally:
            os.close(fd)
    try:
        os.unlink(name, dir_fd=folder_fd)
    except OSError:
        return None
    return data if isinstance(data, dict) else None


def run_writes(writers: dict, active_campaign) -> None:
    """Carry out each waiting write request with the writer for its kind ({kind: fn(payload, campaign)})."""
    if not _DIR_FD:
        return  # the folder can't be read without following links (Windows); the portal's sandbox is macOS-only
    log = rt("owner-writes.log")
    try:
        folder_fd = _open_folder(_writes_folder(), rt(""))
    except OSError:
        return
    try:
        names = sorted(name for name in os.listdir(folder_fd) if name.endswith(".json"))[:WRITES_PER_PASS]
        for name in names:
            request = _take_request(folder_fd, name)
            kind = request.get("kind") if request else None
            if kind not in writers:
                log_line(log, f"dropped an unreadable or unknown write request ({name})")
                continue
            try:
                log_line(log, f"{kind}: {writers[kind](request.get('payload'), active_campaign())}")
            except Exception as error:  # noqa: BLE001 — one bad request never stops the others
                log_line(log, f"{kind} refused: {error}")
    finally:
        os.close(folder_fd)


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
    import npc_describe
    import npc_entries  # noqa: F401 — npc_describe's own import, loaded now with it
    import push_stats  # noqa: F401 — lore_sync imports it on its first run; load it now instead
    return {"lore_sync": lore_sync.run_and_log, "npc_describe": npc_describe.run_and_log}


def _loop(run, *args) -> None:
    while True:
        try:
            run(*args)
        except Exception:  # noqa: BLE001 — the display must keep serving
            pass
        time.sleep(POLL_S)


def start(active_campaign=lambda: None) -> None:
    """Load the helpers and writers now and run requested ones for as long as this process lives.

    `active_campaign()` names the campaign the display is showing; a write request for any other is refused.
    """
    import owner_writes
    threading.Thread(target=_loop, args=(run_due, _helpers()), daemon=True, name="dnd-helper-jobs").start()
    threading.Thread(target=_loop, args=(run_writes, owner_writes.writers(), active_campaign), daemon=True,
                     name="dnd-owner-writes").start()
