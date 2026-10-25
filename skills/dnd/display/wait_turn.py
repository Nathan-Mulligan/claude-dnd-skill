#!/usr/bin/env python3
"""wait_turn.py — one blocking wait that listens to BOTH player channels.

The skill ships two separate waits and neither is sufficient on its own:

  * `autorun_wait.py`     — polls the Party Input queue. Deaf to dice rolls.
  * `send.py --wait`      — polls /dice-request/<id>. Deaf to typed input, and
                            it only reports *that* a roll happened; the actual
                            number is never returned to the DM. It goes into the
                            display's transcript log instead.

So a DM who prescribes a roll is blind to the panel, and a DM waiting on the
panel is blind to the roll — and either way has to ask the player to read the
number out loud, which defeats the point of playing in the browser.

This waits on both at once and prints whatever arrives first:

    python3 display/wait_turn.py                 # just the input panel
    python3 display/wait_turn.py --dice          # panel + any new dice result
    python3 display/wait_turn.py --timeout 540   # default 540s (9 min)

Give the Bash call a timeout longer than --timeout (540000 ms for the
default), or Claude Code kills it at two minutes and the turn is lost.

Output is the same shape autorun_wait.py produces, so it drops straight into
the DM turn loop:

    [Cleric]: I tell her about the marshmallow nose
    [dice] Cleric rolls 1d20+2: [17] +2 = 19 — Deception — convincing Mallyn
"""
import os
import sys
import json
import time
import argparse
import subprocess

DISPLAY = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DISPLAY)
from runtime_paths import rt  # noqa: E402

QUEUE = rt(".input_queue")
LOG   = rt("text_log.json")


def _dice_entries():
    """Every dice-typed line currently in the display transcript."""
    try:
        with open(LOG, encoding="utf-8") as f:
            return [e.get("text", "") for e in json.load(f) if e.get("dice")]
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dice", action="store_true",
                    help="also return when a new dice result lands")
    ap.add_argument("--timeout", type=float, default=540.0)
    ap.add_argument("--poll", type=float, default=0.3)
    args = ap.parse_args()

    # Tell the display a turn is open so the panel goes live and shows its
    # countdown. Mirrors what autorun_wait.py broadcasts.
    subprocess.run([sys.executable, os.path.join(DISPLAY, "push_stats.py"),
                    "--autorun-waiting", "true"], capture_output=True)

    baseline = _dice_entries() if args.dice else []
    deadline = time.time() + args.timeout
    out = []
    heartbeat = time.time()

    while time.time() < deadline:
        # Keep telling the display we're listening; if this process is killed
        # the heartbeats stop and the panel goes back to "not listening".
        if time.time() - heartbeat > 20:
            heartbeat = time.time()
            subprocess.Popen([sys.executable, os.path.join(DISPLAY, "push_stats.py"),
                              "--autorun-waiting", "true"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(QUEUE):
            try:
                with open(QUEUE, encoding="utf-8") as f:
                    content = f.read().strip()
                os.unlink(QUEUE)
                if content:
                    out.append(content)
            except Exception:
                pass

        if args.dice:
            now = _dice_entries()
            if len(now) > len(baseline):
                for line in now[len(baseline):]:
                    out.append(f"[dice] {line}")
                baseline = now

        if out:
            break
        time.sleep(args.poll)

    subprocess.run([sys.executable, os.path.join(DISPLAY, "push_stats.py"),
                    "--autorun-waiting", "false"], capture_output=True)

    if out:
        print("\n".join(out))
        return 0
    return 0   # silent timeout — caller just re-enters the wait


if __name__ == "__main__":
    sys.exit(main())
