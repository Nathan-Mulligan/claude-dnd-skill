#!/usr/bin/env python3
"""scribe.py — the turn log and Recent Events, written in the background while the table reads.

How it runs
  send.py calls `record()` after every narration send. It appends what the table heard to
  runtime/scribe-queue.jsonl and starts this file detached; the DM never waits. One run at a
  time: a run started while another is busy leaves a pending flag, and the busy run goes round
  again. A run waits a few seconds so a roll and its result land as one turn, takes the queue,
  asks the helper model for one line per turn and writes them.

What it writes
  * `<campaign>/turn-log.md`: one dated line per turn. The scribe is its only writer.
  * `## Recent Events` in `<campaign>/state.md`: the newest RECENT_MAX lines. The DM writes the
    rest of state.md, so the edit is an atomic replace that starts again if state.md changed
    while it was being made.

Rules
  * A turn starts at a send with a player block; sends without one join the turn before.
  * The model summarises only text the table heard. With the helper off or failing, the line is
    the turn's opening words instead, and runtime/scribe.log says why.
  * The model is the sidebar-notes helper's choice (lore_sync.py: Luna by default, set in the
    portal). DND_SCRIBE=0 switches the scribe off.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt  # noqa: E402

RECENT_MAX = 6
SETTLE_S = 8.0
LOCK_STALE_S = 600
NEW_SESSION_GAP_S = 3 * 3600
LOG_HEADER = "# Turn log\n\nOne line per turn, written by the scribe. The DM reads it when writing the session recap.\n"
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


def _pending_path() -> str:
    return rt("scribe.pending")


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


def record(campaign_dir: "Path | str", blocks: list) -> None:
    """Queue one send's blocks for the scribe and start a run. Never raises, never blocks."""
    try:
        if not enabled() or not blocks:
            return
        row = {"campaign_dir": str(campaign_dir), "at": time.time(), "blocks": [list(b) for b in blocks]}
        os.makedirs(os.path.dirname(queue_path()), exist_ok=True)
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


def _acquire_lock() -> "int | None":
    lock = rt("scribe.lock")
    try:
        if time.time() - os.path.getmtime(lock) > LOCK_STALE_S:
            os.unlink(lock)
    except OSError:
        pass
    try:
        return os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None


def _release(fd: int) -> None:
    os.close(fd)
    try:
        os.unlink(rt("scribe.lock"))
    except OSError:
        pass


def _take_queue() -> list[dict]:
    path = queue_path()
    taken = path + ".taking"
    try:
        os.replace(path, taken)
    except FileNotFoundError:
        return []
    rows = []
    with open(taken, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    os.unlink(taken)
    return rows


def group_turns(rows: list[dict]) -> list[dict]:
    """[{campaign_dir, at, blocks}] -> one entry per turn, per campaign, in order."""
    turns: list[dict] = []
    for row in rows:
        blocks = [b for b in row.get("blocks") or [] if isinstance(b, list) and len(b) == 2]
        if not blocks:
            continue
        starts = str(blocks[0][0]).startswith("player")
        last = turns[-1] if turns else None
        if last and not starts and last["campaign_dir"] == row.get("campaign_dir"):
            last["blocks"].extend(blocks)
        else:
            turns.append({"campaign_dir": row.get("campaign_dir"), "at": row.get("at") or time.time(), "blocks": blocks})
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


def edit_file(path: Path, edit, tries: int = 4) -> bool:
    """Apply edit(text) -> text by atomic replace, starting again if the file changed meanwhile."""
    for _ in range(tries):
        try:
            before = path.stat()
            text = path.read_text(encoding="utf-8")
        except OSError:
            return False
        new = edit(text)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new)
        os.chmod(tmp, before.st_mode & 0o777)
        now = path.stat()
        if (now.st_mtime_ns, now.st_size) == (before.st_mtime_ns, before.st_size):
            os.replace(tmp, path)
            return True
        os.unlink(tmp)
    return False


def with_recent_events(text: str, lines: list[str]) -> str:
    heading = re.search(r"(?m)^## Recent Events[ \t]*\n", text)
    if not heading:
        return text.rstrip("\n") + "\n\n## Recent Events\n" + "".join(f"- {x}\n" for x in lines[-RECENT_MAX:])
    end = re.search(r"(?m)^## ", text[heading.end():])
    body_end = heading.end() + end.start() if end else len(text)
    old = [line for line in text[heading.end():body_end].splitlines() if re.match(r"\s*[-*]\s+\S", line)]
    keep = (old + [f"- {x}" for x in lines])[-RECENT_MAX:]
    return text[:heading.end()] + "\n".join(keep) + "\n\n" + text[body_end:].lstrip("\n")


def _append_log(folder: Path, entries: list[tuple[float, str]]) -> None:
    path = folder / "turn-log.md"
    try:
        state = json.loads(Path(_state_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    last = float(state.get(str(folder), 0))
    out = "" if path.exists() else LOG_HEADER
    if entries[0][0] - last > NEW_SESSION_GAP_S:
        out += "\n## Play from " + time.strftime("%Y-%m-%d %H:%M", time.localtime(entries[0][0])) + "\n"
    out += "".join(f"- {time.strftime('%H:%M', time.localtime(at))} {line}\n" for at, line in entries)
    with open(path, "a", encoding="utf-8") as f:
        f.write(out)
    state[str(folder)] = entries[-1][0]
    Path(_state_path()).write_text(json.dumps(state), encoding="utf-8")


def write_turns(turns: list[dict]) -> int:
    by_campaign: dict[str, list[dict]] = {}
    for t in turns:
        by_campaign.setdefault(t["campaign_dir"], []).append(t)
    written = 0
    for folder, group in by_campaign.items():
        folder_path = Path(folder)
        if not (folder_path / "state.md").is_file():
            log(f"skipped {len(group)} turn(s): no state.md in {folder}")
            continue
        prompt = _PROMPT.format(n=len(group), turns="\n\n".join(f"Turn {i + 1}:\n{_render(t)}" for i, t in enumerate(group)))
        try:
            lines = summarise(prompt, len(group))
        except Exception as e:  # noqa: BLE001
            log(f"model failed, wrote plain lines: {str(e)[:300]}")
            lines = [plain_line(t) for t in group]
        _append_log(folder_path, [(t["at"], line) for t, line in zip(group, lines)])
        if not edit_file(folder_path / "state.md", lambda text: with_recent_events(text, lines)):
            log("state.md kept changing; Recent Events left for the next turn")
        written += len(group)
    return written


def run(settle_s: float = SETTLE_S, max_passes: int = 4) -> str:
    fd = _acquire_lock()
    if fd is None:
        try:
            Path(_pending_path()).touch()
        except OSError:
            pass
        return "busy"
    total = 0
    try:
        for _ in range(max_passes):
            try:
                os.unlink(_pending_path())
            except OSError:
                pass
            time.sleep(settle_s)
            total += write_turns(group_turns(_take_queue()))
            if not os.path.exists(_pending_path()) and not os.path.exists(queue_path()):
                break
        return f"written {total}"
    finally:
        _release(fd)
        if os.path.exists(queue_path()) and os.path.exists(_pending_path()):
            kick()


if __name__ == "__main__":
    if "--run" in sys.argv:
        try:
            log("run: " + run())
        except Exception as e:  # noqa: BLE001
            log(f"run failed: {type(e).__name__}: {str(e)[:300]}")
    else:
        print(__doc__)
