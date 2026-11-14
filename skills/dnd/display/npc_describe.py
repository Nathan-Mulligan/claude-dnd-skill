#!/usr/bin/env python3
"""npc_describe.py — copy what the narration said about an NPC's looks and manner, in the background.

How it runs
  records.py (inside send.py) calls `request()` when an NPC who speaks has no look line in
  npcs.md. It queues the narration the table heard in runtime/npc-describe-queue.jsonl and
  starts this file detached; the DM never waits. One worker at a time, under a lock the OS
  drops when the worker exits. Results wait in runtime/npc-describe.json until the next send,
  when records.py adds them to npcs.md as `- **As seen:**`. So the DM's own call is the only
  thing that writes npcs.md, and a portal save never sees it change under its feet.

Rules
  * Extraction only. The model returns phrases copied from the narration; a phrase that is
    not in the narration word for word is dropped, so nothing invented reaches npcs.md.
    When the narration says nothing about the person, nothing is written.
  * At most MAX_TRIES requests per person; after that the DM's own notes are all there is.
  * The model is the sidebar-notes helper's choice (lore_sync.py: off, Luna by default, set
    in the portal), on a subscription or local model only. DND_LORE_SYNC=0 switches it off.
  * Any failure is silent apart from runtime/npc_describe.log.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt  # noqa: E402

import npc_entries  # noqa: E402

try:
    import fcntl
except ImportError:  # Windows: no flock, so two workers may overlap; each result is still checked
    fcntl = None

MAX_TRIES = 3
MAX_PHRASES = 3
PHRASE_MAX = 160
TEXT_MAX = 6000
MAX_PASSES = 5
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["phrases"],
    "properties": {"phrases": {"type": "array", "items": {"type": "string"}}},
}
PROMPT = """Below is narration the players of a tabletop game were just shown.

Copy, word for word, up to {limit} short phrases from it that describe how {name} looks, dresses,
sounds or carries themselves. Copy only words about {name}, exactly as they are written, as JSON
{{"phrases": [...]}}. Never add, guess, reword or summarise. If the narration describes none of
that for {name}, return {{"phrases": []}}. Do not run commands.

NARRATION:
{text}
"""
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "—": "-", "–": "-"})


def queue_path() -> str:
    return rt("npc-describe-queue.jsonl")


def results_path() -> str:
    return rt("npc-describe.json")


def log(msg: str) -> None:
    try:
        with open(rt("npc_describe.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def _choice():
    import lore_sync
    return lore_sync.choose_model()


def _load() -> dict:
    try:
        data = json.loads(Path(results_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    path = Path(results_path())
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _campaign(data: dict, folder: str) -> dict:
    entry = data.setdefault(folder, {})
    entry.setdefault("done", {})
    entry.setdefault("tries", {})
    return entry


def request(folder: Path, name: str, narration: str) -> bool:
    """Queue a look-up for one person and start a worker. Returns True when queued. Never raises."""
    try:
        if _choice() is None:
            return False
        key, who = str(Path(folder).resolve()), npc_entries.norm_name(name)
        with npc_entries.locked("npc-describe"):
            entry = _campaign(_load(), key)
            if entry["done"].get(who) or int(entry["tries"].get(who, 0)) >= MAX_TRIES:
                return False
            with open(queue_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps({"folder": key, "name": name, "text": narration[-TEXT_MAX:]},
                                   ensure_ascii=False) + "\n")
        kick()
        return True
    except Exception as error:  # noqa: BLE001
        log(f"request failed: {error}")
        return False


def results(folder: Path) -> dict:
    """Descriptions found for this campaign and not yet acknowledged, as {name: text}."""
    try:
        with npc_entries.locked("npc-describe"):
            entry = _load().get(str(Path(folder).resolve()))
        done = entry.get("done") if isinstance(entry, dict) else None
        return {item["name"]: item["text"] for item in (done or {}).values()
                if isinstance(item, dict) and isinstance(item.get("name"), str) and item.get("text")}
    except Exception as error:  # noqa: BLE001
        log(f"results failed: {error}")
        return {}


def acknowledge(folder: Path, names: list) -> None:
    """Mark these descriptions as written to the NPC file; the person is never asked about again."""
    if not names:
        return
    try:
        with npc_entries.locked("npc-describe"):
            data = _load()
            done = _campaign(data, str(Path(folder).resolve()))["done"]
            for name in names:
                item = done.get(npc_entries.norm_name(name))
                if isinstance(item, dict):
                    item["text"] = ""
            _save(data)
    except Exception as error:  # noqa: BLE001
        log(f"acknowledge failed: {error}")


def kick() -> None:
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--run"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except Exception as error:  # noqa: BLE001
        log(f"kick failed: {error}")


def _plain(text: str) -> str:
    return " ".join(text.translate(_QUOTES).split())


def copied_phrases(reply: object, narration: str, name: str) -> list:
    """The reply's phrases found in the narration as whole words (ignoring case, spacing and quote style),
    each returned as the narration wrote it."""
    phrases = reply.get("phrases") if isinstance(reply, dict) else None
    if not isinstance(phrases, list):
        raise ValueError("model reply is not {phrases: [...]}")
    source = _plain(narration)
    folded = source.lower()
    kept: list = []
    for phrase in phrases:
        if not isinstance(phrase, str):
            continue
        wanted = _plain(phrase).strip(" ,;.").lower()
        if len(wanted) < 4 or len(wanted) > PHRASE_MAX or wanted == npc_entries.norm_name(name):
            continue
        match = re.search(r"(?<!\w)" + re.escape(wanted) + r"(?!\w)", folded)
        if not match:
            continue
        copied = source[match.start():match.end()] if len(folded) == len(source) else phrase.strip(" ,;.")
        if any(wanted in k.lower() for k in kept):
            continue
        kept.append(copied)
        if len(kept) == MAX_PHRASES:
            break
    return kept


def describe(name: str, narration: str, choice=None) -> str:
    import lore_sync
    reply = lore_sync.ask(PROMPT.format(limit=MAX_PHRASES, name=name, text=narration[-TEXT_MAX:]), SCHEMA, choice)
    return "; ".join(copied_phrases(reply, narration, name))


def _read_queue() -> list:
    rows = []
    try:
        with open(queue_path(), encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and all(isinstance(row.get(k), str) for k in ("folder", "name", "text")):
                    rows.append(row)
    except FileNotFoundError:
        pass
    return rows


def _take_queue() -> list:
    with npc_entries.locked("npc-describe"):
        rows = _read_queue()
        try:
            os.unlink(queue_path())
        except FileNotFoundError:
            pass
    return rows


def process(rows: list, choice=None) -> int:
    """Look up each queued person once (their queued narration joined). Returns how many were found."""
    jobs: dict = {}
    for row in rows:
        job = jobs.setdefault((row["folder"], npc_entries.norm_name(row["name"])), {"name": row["name"], "text": []})
        if row["text"] not in job["text"]:
            job["text"].append(row["text"])
    found = 0
    for (folder, who), job in jobs.items():
        with npc_entries.locked("npc-describe"):
            data = _load()
            entry = _campaign(data, folder)
            if entry["done"].get(who) or int(entry["tries"].get(who, 0)) >= MAX_TRIES:
                continue
            entry["tries"][who] = int(entry["tries"].get(who, 0)) + 1
            _save(data)
        try:
            text = describe(job["name"], "\n\n".join(job["text"])[-TEXT_MAX:], choice)
        except Exception as error:  # noqa: BLE001
            import lore_sync
            log(f"{job['name']}: model failed: {lore_sync.failure_reason(error)}")
            continue
        if not text:
            continue
        with npc_entries.locked("npc-describe"):
            data = _load()
            _campaign(data, folder)["done"][who] = {"name": job["name"], "text": text}
            _save(data)
        found += 1
    return found


def run() -> str:
    os.makedirs(os.path.dirname(rt("npc-describe.lock")), exist_ok=True)
    fd = os.open(rt("npc-describe.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "busy"  # the running worker re-checks the queue when it lets go
        found = 0
        for _ in range(MAX_PASSES):
            rows = _take_queue()
            if not rows:
                break
            found += process(rows)
    finally:
        os.close(fd)
    if os.path.exists(queue_path()):  # queued while this worker held the lock
        kick()
    return f"found {found}"


if __name__ == "__main__":
    if "--run" in sys.argv:
        try:
            log("run: " + run())
        except Exception as error:  # noqa: BLE001
            log(f"run failed: {type(error).__name__}: {str(error)[:300]}")
    else:
        print(__doc__)
