"""The newest completed passage keeps its text and receives a listen button."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


HTML = Path(__file__).resolve().parents[1] / "skills/dnd/display/templates/index.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_js(source):
    result = subprocess.run([NODE, "-"], input=source, check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(result.stdout)


def test_finalising_live_narration_preserves_its_paragraph_for_replay():
    source = HTML.read_text(encoding="utf-8")
    function = re.search(r"function flushNewBlock\(\) \{.*?\n\}", source, re.S).group()
    result = run_js("""
let removed = 0, listenButtons = 0;
let currentCursor = {parentNode:{removeChild:()=>removed++}, classList:{remove:()=>{}}};
let currentEl = {textContent:'The latest passage'};
const textContent = {appendChild:()=>{}};
const document = {createElement:()=>({})};
function _addBlockBadge() {}
function _addTtsBar(block) { if (block.textContent) listenButtons++; }
""" + function + """
flushNewBlock();
console.log(JSON.stringify({removed, listenButtons, closed:currentEl===null}));
""")
    assert result == {"removed": 0, "listenButtons": 1, "closed": True}


def test_live_narration_finalisation_waits_for_long_typewriter():
    source = HTML.read_text(encoding="utf-8")
    callback = re.search(r"const flushAfter = \(\) => \{.*?\n  \};", source, re.S).group()
    result = run_js("""
let _openerHoldUntil = 0, isTyping = true, charQueue = ['text'], idleTimer;
let completed = 0;
const timers = [];
function setTimeout(callback) { timers.push(callback); return timers.length; }
function flushNewBlock() { completed++; }
""" + callback + """
flushAfter();
const waited = timers.length === 1 && completed === 0;
isTyping = false; charQueue = [];
if (timers.length) timers.shift()();
console.log(JSON.stringify({waited, completed, pending:timers.length}));
""")
    assert result == {"waited": True, "completed": 1, "pending": 0}
