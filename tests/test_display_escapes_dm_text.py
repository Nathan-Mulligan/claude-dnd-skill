"""The display shows text the DM wrote (sheets, stats, award blocks) as text, never as markup (#160).

Driven through node against the real template functions, with a small stand-in DOM that records
every string given to innerHTML or insertAdjacentHTML.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / "skills" / "dnd" / "display" / "templates" / "index.html"
NODE = shutil.which("node")
X = "<img src=x onerror=alert(1)>"
SHOWN = "&lt;img src=x onerror=alert(1)&gt;"

_DOM = """
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.style = {};
    this.className = ''; this._text = ''; this._html = ''; const self = this;
    this.classList = { add(c) { self.className = (self.className + ' ' + c).trim(); }, remove() {},
                       toggle() {}, contains() { return false; } }; }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { cs.forEach(c => this.appendChild(typeof c === 'string' ? document.createTextNode(c) : c)); }
  addEventListener() {} setAttribute() {} remove() {}
  querySelector() { return null; } querySelectorAll() { return []; }
  set textContent(v) { this._text = String(v); this.children = []; this._html = ''; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set innerHTML(v) { this._html = String(v); this.children = []; this._text = ''; }
  get innerHTML() { return this._html; }
  insertAdjacentHTML(_, v) { this._html += String(v); }
}
const _byId = {};
const document = {
  createElement: tag => new El(tag),
  createTextNode: text => { const n = new El('#text'); n.textContent = text; return n; },
  getElementById: id => (_byId[id] = _byId[id] || new El('div')),
};
const markup = el => el._html + el.children.map(markup).join('');
const X = %s;
"""


def _defs(*names: str) -> str:
    text = HTML.read_text(encoding="utf-8")
    out = []
    for name in names:
        m = re.search(rf"^(?:function {name}\(|const {name} = ).*?^}};?$", text, re.S | re.M)
        assert m, f"{name} not found in the template"
        out.append(m.group(0))
    return "\n".join(out)


def _sheet_helpers() -> str:
    m = re.search(r"(function _isCurrencyLine.*?)\nfunction openSheet\(", HTML.read_text(encoding="utf-8"), re.S)
    assert m, "sheet helpers not found in the template"
    return m.group(1)


def _run(prelude: str, script: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
        f.write(_DOM % json.dumps(X) + prelude + "\n" + script)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=60)
    finally:
        pathlib.Path(path).unlink()
    assert out.returncode == 0, out.stderr[:2000]
    return json.loads(out.stdout.strip().splitlines()[-1])


pytestmark = pytest.mark.skipif(not NODE, reason="node not available")
CARD = _defs("_escHtml", "_CLASS_ICONS", "_classIconSrc", "_hpColor", "_buildPlayerCard") if HTML.exists() else ""


def test_sidebar_card_shows_dm_written_fields_as_text():
    r = _run(CARD, """
    const card = _buildPlayerCard({name: 'Ana', race: X, class: 'Rogue/' + X, background: X, level: X,
      hp: {current: X, max: X}, xp: {current: X, next: X}, ac: X, initiative: X, speed: X,
      hit_dice: {remaining: X, max: X, die: X}}, true);
    const html = markup(card);
    console.log(JSON.stringify({raw: html.includes('<img src=x'), shown: html.split('&lt;img').length - 1}));
    """)
    assert r["raw"] is False
    assert r["shown"] >= 12


def test_sidebar_card_keeps_its_own_formatting():
    r = _run(CARD, """
    const card = _buildPlayerCard({name: 'Ana', race: 'Wood Elf', class: 'Rogue/Thief', background: 'Sage & Scribe',
      level: 3, hp: {current: 7, max: 21}, ac: 14, initiative: '+3', speed: 35}, true);
    const ident = card.children.find(c => c.className === 'sb-identity');
    const quick = card.children.find(c => c.className === 'sb-quick-stats');
    console.log(JSON.stringify({ident: ident.innerHTML, quick: quick.innerHTML}));
    """)
    assert r["ident"] == 'Wood Elf · Rogue 3<br><span style="opacity:0.7">Thief · Sage &amp; Scribe</span>'
    assert r["quick"] == ('AC <span class="val">14</span><br>Init <span class="val">+3</span>'
                          '<br>Spd <span class="val">35</span>')


def test_character_sheet_shows_dm_written_fields_as_text():
    prelude = CARD + "\n" + _defs("_smDivider") + "\n" + _sheet_helpers() + "\n" + _defs("openSheet")
    r = _run("const _playerData = {}; let _openSheetName = null;\n" + prelude, """
    _playerData.Ana = {name: 'Ana', race: X, class: 'Wizard', level: 1, hp: {current: X, max: X}, ac: X,
      initiative: X, speed: X, hit_dice: {remaining: X, max: X, die: X},
      ability_scores: {str: {score: X, mod: X}},
      sheet: {attacks: [{name: X, bonus: X, damage: X, type: X, notes: X}]}};
    openSheet('Ana');
    const html = markup(document.getElementById('sheet-content'));
    console.log(JSON.stringify({raw: html.includes('<img src=x'), notes: html.includes('<td class="atk-notes">' + %s)}));
    """ % json.dumps(SHOWN))
    assert r == {"raw": False, "notes": True}


def test_award_blocks_and_the_queue_show_names_as_text():
    stubs = ("let textContent = new El('div'); const _waitingEl = new El('div');\n"
             "function _flushForBlock() {} function _reanchorCursor() {} function scrollToBottom() {}\n"
             "function _queuedLabel() { return 'Queued'; }\n")
    r = _run(stubs + _defs("_escHtml", "_renderInspirationBlockNow", "_renderMilestoneBlockNow",
                           "_renderQueueStatus"), """
    _renderInspirationBlockNow(X, 'kind words');
    _renderMilestoneBlockNow(X, X, '', false);
    _renderQueueStatus([X]);
    const html = markup(textContent) + markup(_waitingEl);
    console.log(JSON.stringify({raw: html.includes('<img src=x'), shown: html.split('&lt;img').length - 1}));
    """)
    assert r == {"raw": False, "shown": 4}


# Every value put into markup is escaped, unless it is one of these, which never carry DM text
# (constants, numbers the display computes, markup already escaped where it was built).
SAFE = {
    "isWarning ? '⚠' : '◈'", "isWarning ? 'Warning' : 'DM Hint'", "dim ? 'rgba(200,185,150,0.25)' : 'rgba(140,190,140,0.6)'",
    "f", "label", "key.toUpperCase()", "iconSrc", "linkHtml", "didYouMean", "chips", "checks", "_queuedLabel()",
    "s.id", "s.label", "s.id === current ? ' selected' : ''", "lvl", "inline(m[2])", "inline(h)", "inline(c)", "t",
    "result.spec", "result.total", "result.advantage", "result.both.join(' / ')",
    "result.modifier ? (result.modifier > 0 ? '+' : '') + result.modifier : ''",
}
_SINK = re.compile(r"\.innerHTML\s*=|\.insertAdjacentHTML\(")
_MARKUP_TEMPLATE = re.compile(r"`[^`]*<[a-zA-Z/][^`]*\$\{|\$\{[^`]*`[^`]*<[a-zA-Z/]")
_INTERPOLATION = re.compile(r"\$\{([^{}`]*)\}")
_ESCAPED = re.compile(r"^_escHtml\((.*)\)$", re.S)


def _sink_statements(text: str):
    """Each innerHTML or insertAdjacentHTML statement, and each line building a markup template."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _SINK.search(line):
            j = i
            while not lines[j].rstrip().endswith(";") and j + 1 < len(lines):
                j += 1
            yield i + 1, "\n".join(lines[i:j + 1])
        elif _MARKUP_TEMPLATE.search(line):
            yield i + 1, line


def test_every_value_in_the_templates_markup_is_escaped():
    loose = []
    for number, statement in _sink_statements(HTML.read_text(encoding="utf-8")):
        for expr in _INTERPOLATION.findall(statement):
            expr = expr.strip()
            if not (_ESCAPED.match(expr) or expr in SAFE):
                loose.append(f"line {number}: ${{{expr}}}")
    assert loose == []


def test_the_escape_also_escapes_quotes_for_attributes():
    r = _run(_defs("_escHtml"), """console.log(JSON.stringify([_escHtml('a"b\\'c<d>&'), _escHtml(null), _escHtml(7)]));""")
    assert r == ["a&quot;b&#39;c&lt;d&gt;&amp;", "", "7"]
