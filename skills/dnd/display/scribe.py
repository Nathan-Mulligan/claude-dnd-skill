#!/usr/bin/env python3
"""scribe.py — the turn log, written in the background while the table reads.

How it runs
  send.py calls `record()` after a narration send. It appends what the table heard to
  runtime/scribe-queue.jsonl and starts this file detached; the DM never waits. One worker
  at a time, under a lock the operating system drops when the worker exits. The worker waits
  a few seconds so a roll and its result land together, summarises the queued turns with the
  helper model, appends them to `<campaign>/turn-log.md` and only then removes them from the
  queue, so a crash loses nothing.

What it writes
  * `<campaign>/turn-log.md` only. The scribe is its only writer and never touches state.md,
    so it can never overwrite the DM or encounter_state.py.
  * A `## Session from <date time>` heading before the first line after each save (when
    session-tail.md is newer than the last line logged).

Rules
  * A turn starts at a send with a player block. A send without one continues the turn
    before it; when that turn is already logged, it is logged as an indented continuation.
  * The model summarises only text the table heard. With the helper off or failing, the line
    is the turn's opening words, and runtime/scribe.log says why.
  * The model is the sidebar-notes helper's choice (lore_sync.py: Luna by default, set in the
    portal), on the subscription only. DND_SCRIBE=0 switches the scribe off.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt  # noqa: E402

try:
    import fcntl
except ImportError:  # Windows: no flock; the queue lock falls back to an exclusive lock file
    fcntl = None

SETTLE_S = 8.0
MAX_PASSES = 6
LOG_HEADER = "# Turn log\n\nOne line per turn, written by the scribe from what the table heard.\n"
_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["lines"],
    "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
}
_PROMPT = """You keep the play log of a tabletop role-playing game. Below are {n} turns, each the exact
text the players heard. Write one line per turn, in order, as JSON {{"lines": [...]}} with exactly {n}
strings. Each line is one past-tense sentence of at most 35 words: who did what, where, and any
names, prices, promises, deadlines or open questions. Use only facts in the text. Never invent,
never judge, never address the players.

{turns}"""


def queue_path() -> str:
    return rt("scribe-queue.jsonl")


def log_path() -> str:
    return rt("scribe.log")


def _state_path() -> str:
    return rt("scribe-state.json")


def enabled() -> bool:
    return os.environ.get("DND_SCRIBE", "1").strip() != "0"


def log(msg: str) -> None:
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


@contextmanager
def _locked(name: str, wait: bool = True):
    """Hold runtime/<name> exclusively; yields False when wait=False and another holder has it."""
    os.makedirs(os.path.dirname(rt(name)), exist_ok=True)
    fd = os.open(rt(name), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is None:
            yield True
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)  # closing drops the lock, and so does the process exiting


def record(campaign_dir: "Path | str", blocks: list) -> None:
    """Queue one send's delivered blocks for the scribe and start a worker. Never raises or blocks."""
    try:
        if not enabled() or not blocks:
            return
        row = {"id": uuid.uuid4().hex, "campaign_dir": str(campaign_dir), "at": time.time(),
               "starts_turn": str(blocks[0][0]).startswith("player"), "blocks": [list(b) for b in blocks]}
        with _locked("scribe-queue.lock"):
            with open(queue_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        kick()
    except Exception as e:  # noqa: BLE001 — the narration already reached the table
        log(f"record failed: {e}")


def kick() -> None:
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--run"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except Exception as e:  # noqa: BLE001
        log(f"kick failed: {e}")


def _read_queue() -> list[dict]:
    rows = []
    with _locked("scribe-queue.lock"):
        try:
            with open(queue_path(), encoding="utf-8") as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except FileNotFoundError:
            pass
    return rows


def _drop_from_queue(ids: set) -> None:
    with _locked("scribe-queue.lock"):
        try:
            with open(queue_path(), encoding="utf-8") as f:
                keep = [line for line in f if line.strip() and _row_id(line) not in ids]
        except FileNotFoundError:
            return
        tmp = queue_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(tmp, queue_path())


def _row_id(line: str) -> "str | None":
    try:
        return json.loads(line).get("id")
    except ValueError:
        return None


def group_turns(rows: list[dict]) -> list[dict]:
    """Queue rows -> turns, per campaign, in order; a first row that continues a logged turn is marked."""
    turns: list[dict] = []
    for row in rows:
        blocks = [b for b in row.get("blocks") or [] if isinstance(b, list) and len(b) == 2]
        if not blocks:
            continue
        last = next((t for t in reversed(turns) if t["campaign_dir"] == row.get("campaign_dir")), None)
        if last and not row.get("starts_turn"):
            last["blocks"].extend(blocks)
        else:
            turns.append({"campaign_dir": row.get("campaign_dir"), "at": row.get("at") or time.time(),
                          "blocks": blocks, "continues": not row.get("starts_turn")})
    return turns


def _render(turn: dict) -> str:
    return "\n".join(f"[{kind}] {text}" for kind, text in turn["blocks"])


def summarise(prompt: str, n: int) -> list[str]:
    import lore_sync
    result = lore_sync.ask(prompt, _SCHEMA)
    lines = result.get("lines") if isinstance(result, dict) else None
    if not isinstance(lines, list) or len(lines) != n or not all(isinstance(x, str) and x.strip() for x in lines):
        raise ValueError(f"model reply is not {n} lines")
    return [" ".join(x.split()) for x in lines]


def plain_line(turn: dict) -> str:
    narration = " ".join(text for kind, text in turn["blocks"] if kind == "narration") or _render(turn)
    first = re.split(r"(?<=[.!?])\s+", " ".join(narration.split()), maxsplit=2)
    line = " ".join(first[:2])
    return line if len(line) <= 240 else line[:240].rsplit(" ", 1)[0] + "..."


def _load_state() -> dict:
    try:
        return json.loads(Path(_state_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _append_log(folder: Path, entries: list[tuple[dict, str]]) -> None:
    path = folder / "turn-log.md"
    state = _load_state()
    last = float(state.get(str(folder), 0))
    try:
        saved = (folder / "session-tail.md").stat().st_mtime
    except OSError:
        saved = 0.0
    out = "" if path.exists() else LOG_HEADER
    if not last or saved > last:
        out += "\n## Session from " + time.strftime("%Y-%m-%d %H:%M", time.localtime(entries[0][0]["at"])) + "\n"
    for turn, line in entries:
        stamp = time.strftime("%H:%M", time.localtime(turn["at"]))
        out += f"  - {stamp} (continued) {line}\n" if turn["continues"] and last else f"- {stamp} {line}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(out)
    state[str(folder)] = max(t["at"] for t, _ in entries)
    tmp = _state_path() + ".tmp"
    Path(tmp).write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, _state_path())


def write_turns(turns: list[dict]) -> int:
    by_campaign: dict[str, list[dict]] = {}
    for t in turns:
        by_campaign.setdefault(t["campaign_dir"], []).append(t)
    written = 0
    for folder, group in by_campaign.items():
        folder_path = Path(folder)
        if not (folder_path / "state.md").is_file():
            log(f"dropped {len(group)} turn(s): no state.md in {folder}")
            continue
        prompt = _PROMPT.format(n=len(group), turns="\n\n".join(f"Turn {i + 1}:\n{_render(t)}" for i, t in enumerate(group)))
        try:
            lines = summarise(prompt, len(group))
        except Exception as e:  # noqa: BLE001
            log(f"model failed, wrote plain lines: {str(e)[:300]}")
            lines = [plain_line(t) for t in group]
        _append_log(folder_path, list(zip(group, lines)))
        written += len(group)
    return written


def run(settle_s: float = SETTLE_S) -> str:
    with _locked("scribe.lock", wait=False) as held:
        if not held:
            return "busy"  # the holder re-checks the queue after it lets go
        total = 0
        for _ in range(MAX_PASSES):
            time.sleep(settle_s)
            rows = _read_queue()
            if not rows:
                break
            total += write_turns(group_turns(rows))
            _drop_from_queue({r.get("id") for r in rows})
    if _read_queue():
        kick()
    return f"written {total}"


if __name__ == "__main__":
    if "--run" in sys.argv:
        try:
            log("run: " + run())
        except Exception as e:  # noqa: BLE001
            log(f"run failed: {type(e).__name__}: {str(e)[:300]}")
    else:
        print(__doc__)
