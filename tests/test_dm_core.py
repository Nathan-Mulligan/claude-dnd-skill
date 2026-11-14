"""The DM's boot reading: a short core file it reads in full, and links that resolve.

On Codex the DM once read SKILL.md, SKILL-commands.md and SKILL-scripts.md in
one command and the tool kept about 40,000 of 198,000 characters. Everything
from Standard 13 on never arrived. DM-CORE.md is the fix: short enough to read
whole, and the boot instructions point at it first.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills" / "dnd"
CORE = SKILL / "DM-CORE.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_dm_core_fits_in_one_read_and_ends_with_a_check_phrase():
    lines = _read(CORE).splitlines()
    assert len(lines) <= 150
    assert lines[-1].startswith("Check phrase: ")
    assert len(lines[-1]) > len("Check phrase: ") + 3


@pytest.mark.parametrize("needle", [
    "--dice-request", "--dc", "--wait", "oracle.py npc", "calendar.py",
    "arc.py advance", "arc.py revise", "--scene", "## Next Hooks", "## Clocks",
    "Turn card", "Trade policy", "spotlight", "dictated", "source/",
    "npc note", "--no-road", "--name", "inventory.py", "yield_time_ms",
])
def test_dm_core_carries_the_play_rules(needle):
    assert needle in _read(CORE)


def test_agents_md_sends_a_portal_dm_to_dm_core_in_chunks():
    text = _read(REPO / "AGENTS.md")
    assert "skills/dnd/DM-CORE.md" in text
    assert "Houston pre-prompt" in text
    assert "200 lines" in text
    assert "grep -n" in text and "sed -n" in text
    assert text.index("DM-CORE.md") < text.index("SKILL.md")


def test_skill_md_points_at_dm_core_before_the_standards():
    text = _read(SKILL / "SKILL.md")
    assert text.index("DM-CORE.md") < text.index("## What Makes a Great DM")
    assert "dark, immersive" not in text


@pytest.mark.parametrize("heading", [
    "### Ruleset: 2014 and 2024", "### Guided entry", "### Model Routing",
    "### Autorun / taxi mode", "### Display sync", "### Tutor Mode",
])
def test_plumbing_sits_under_the_reference_heading(heading):
    text = _read(SKILL / "SKILL.md")
    assert text.index("\n## Reference") < text.index("\n" + heading + "\n")
    assert text.index("## What Makes a Great DM") < text.index("\n## Reference")


def _slug(heading: str) -> str:
    text = heading.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    out = set()
    in_fence = False
    for line in _read(path).splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if in_fence:
            continue
        m = re.match(r"#{1,6}\s+(.*)", line)
        if not m:
            continue
        slug = _slug(m.group(1))
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f"{slug}-{n}")
    return out


DOCS = [*sorted(SKILL.glob("*.md")), REPO / "AGENTS.md", REPO / ".qwen/skills/dnd-local/SKILL.md"]


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO)))
def test_relative_links_and_anchors_resolve(doc):
    for target in re.findall(r"\]\(([^)\s]+)\)", _read(doc)):
        if re.match(r"[a-z]+:", target):
            continue
        file_part, _, anchor = target.partition("#")
        dest = (doc.parent / file_part).resolve() if file_part else doc
        assert dest.exists(), f"{doc.name}: {target}"
        if anchor and dest.suffix == ".md":
            assert anchor in _anchors(dest), f"{doc.name}: {target}"


def _portal():
    spec = importlib.util.spec_from_file_location("_portal_dm_core", REPO / "scripts/campaign_portal.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mode,onboarding", [("new", None), ("resume", None), ("new", {"characters": [{}]})])
def test_portal_launch_prompt_sends_the_dm_to_dm_core(mode, onboarding):
    prompt = _portal().initial_prompt(mode, "adventure", "", onboarding)
    assert "skills/dnd/DM-CORE.md" in prompt
    assert "AGENTS.md" in prompt


def test_portal_resume_skips_setup_questions_and_opens_moving():
    prompt = _portal().initial_prompt("resume", "adventure", "")
    assert "display and dice questions" in prompt
    assert "Next Hooks" in prompt


def _sender(tmp_path):
    spec = importlib.util.spec_from_file_location("_send_last_line_test", SKILL / "display" / "send.py")
    with mock.patch.dict(os.environ, {"DND_RUNTIME_DIR": str(tmp_path)}):
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def _send(sender, argv, body):
    sent = []
    with mock.patch.object(sys, "argv", ["send.py", *argv]), \
         mock.patch.object(sys, "stdin", io.StringIO(body)), \
         mock.patch.object(sender, "_post", side_effect=lambda *args: sent.append(args) or True), \
         mock.patch.dict(os.environ, {"DND_ACTIVE_CAMPAIGN": ""}):
        sender.main()
    return sent


@pytest.mark.parametrize("ending", [
    "Elbane, Nella, what would you like to do?",
    "Where would you like to go first?",
    "Where to next?",
    "What do you do?",
    "So, what do you do now?",
    'The guard shouts, "Halt!" What do you do?',
    "The guard shouts, “Halt!” Where to next?",
    "The bell on the bridge starts ringing; the gate will close before you reach it. What do you do?",
])
def test_narration_ending_on_a_bare_question_warns_but_still_sends(tmp_path, capsys, ending):
    sender = _sender(tmp_path)
    sent = _send(sender, [], f"The wagon creaks to a stop outside Saltmere.\n\n{ending}\n")
    assert sent, "the warning must never block the send"
    err = capsys.readouterr().err
    assert "something in motion" in err


@pytest.mark.parametrize("argv,body", [
    ([], "Hessa slams the ledger shut. \"Twenty silver by dusk, or the wagon stays.\""),
    ([], "The bell on the bridge starts ringing. Where do you two go from here, with the gate closing?"),
    (["--npc", "Tovin"], "Where to next?"),
    (["--opener"], "Where to next?"),
])
def test_pressure_endings_and_other_blocks_do_not_warn(tmp_path, capsys, argv, body):
    sender = _sender(tmp_path)
    assert _send(sender, argv, body)
    assert "something in motion" not in capsys.readouterr().err


class _Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode("utf-8"))


def test_dice_request_wait_prints_the_roll_so_the_dm_resolves_in_the_same_turn(tmp_path, capsys):
    sender = _sender(tmp_path)
    replies = iter([
        _Response({"request_id": "abc", "pending": ["Nella"]}),
        _Response({"complete": True, "pending": [],
                   "results": ["Nella rolls 1d20+3: [14] +3 = 17 — Persuasion"]}),
    ])
    with mock.patch.object(sys, "argv", ["send.py", "--dice-request", "--character", "Nella",
                                         "--modifier", "3", "--dc", "13", "--wait"]), \
         mock.patch.object(sender.urllib.request, "urlopen", side_effect=lambda *a, **k: next(replies)), \
         mock.patch.dict(os.environ, {"DND_ACTIVE_CAMPAIGN": ""}):
        sender.main()
    captured = capsys.readouterr()
    assert "Nella rolls 1d20+3: [14] +3 = 17" in captured.out
    assert "yield_time_ms 120000" in captured.err, "a Codex DM must wait once, not poll"


def test_display_reports_the_rolled_result_for_a_finished_request(tmp_path):
    spec = importlib.util.spec_from_file_location("_dice_result_app_test", SKILL / "display" / "dnd-display-app.py")
    with mock.patch.dict(os.environ, {"DND_RUNTIME_DIR": str(tmp_path)}):
        app = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app)
    app._broadcast = lambda payload: None
    app._persist_log = lambda: None
    app._persist_tail = lambda: None
    app._token_ok = lambda: True
    client = app.app.test_client()

    request_id = client.post("/dice-request", json={"character": "Nella", "modifier": 3, "dc": 13}).json["request_id"]
    roll = client.post("/player-input/dice", json={"character": "Nella", "spec": "1d20", "modifier": 3,
                                                    "label": "Persuasion", "request_id": request_id}).json
    status = client.get(f"/dice-request/{request_id}").json

    assert status["complete"] is True
    assert status["results"] == [roll["text"]]


def test_dm_core_keeps_the_resume_load_lean_and_documents_the_wrap_up():
    core = _read(CORE)
    for needle in ("stop_point.py show", "stop_point.py write", "## Story So Far",
                   "session-log-archive.md", "Save & close", "exact moment"):
        assert needle in core


PLAY = SKILL / "play"
SITUATION_FILES = ["combat", "travel", "trade", "npc-rules", "scene-media", "new-campaign", "save"]


@pytest.mark.parametrize("name", SITUATION_FILES)
def test_each_situation_file_is_one_short_read_named_by_dm_core(name):
    path = PLAY / f"{name}.md"
    assert path.is_file()
    assert len(_read(path).splitlines()) <= 40
    assert f"`{name}.md`" in _read(CORE)


@pytest.mark.parametrize("name,needles", [
    ("combat", ["encounter_state.py start", "--music-event combat-start", "--turn-clear", "xp.py award"]),
    ("travel", ["advance N days", "--no-road", "## Next Hooks", "## Clocks", "seen by:"]),
    ("trade", ["Trade policy:", "inventory.py buy", "@tutor", "10% per band"]),
    ("npc-rules", ["oracle.py npc --campaign", "voice=", "npc note"]),
    ("scene-media", ["--scene", "--mood", "combat-end"]),
    ("save", ["stop_point.py write", "Save & close", "turn-log.md", "## Story So Far", "session-log-archive.md"]),
    ("new-campaign", ["--setup-question", "push_stats.py --party", "paths.py campaign-path"]),
])
def test_situation_files_carry_their_rules(name, needles):
    text = _read(PLAY / f"{name}.md")
    for needle in needles:
        assert needle in text, (name, needle)
