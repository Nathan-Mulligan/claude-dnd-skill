"""The character sheet renders the ledger: purse, grouped item table, shared cargo, features.

Driven through node against the real template functions, with a small stand-in
DOM, so a renamed field or a broken grouping fails here rather than on the
table's screen. A character without a ledger keeps tonight's purse-from-prose.
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
_SLICE = re.compile(r"(function _isCurrencyLine.*?)\nfunction openSheet\(", re.S)

_DOM = """
class El {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
    this.className = ''; this._text = ''; const self = this;
    this.classList = { add(c) { self.className = (self.className + ' ' + c).trim(); } }; }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { cs.forEach(c => this.appendChild(c)); }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  all(pred, out = []) { this.children.forEach(c => { if (pred(c)) out.push(c); c.all(pred, out); }); return out; }
  cls(name) { return this.all(c => (' ' + c.className + ' ').includes(' ' + name + ' ')); }
}
const document = { createElement: tag => new El(tag) };
"""


def _run(script: str) -> dict:
    m = _SLICE.search(HTML.read_text(encoding="utf-8"))
    assert m, "sheet helpers not found in the template"
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
        f.write(_DOM + m.group(1) + "\n" + script)
        path = f.name
    out = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=60)
    pathlib.Path(path).unlink()
    assert out.returncode == 0, out.stderr[:2000]
    return json.loads(out.stdout.strip().splitlines()[-1])


pytestmark = pytest.mark.skipif(not NODE, reason="node not available")


def test_purse_comes_from_the_ledger_and_falls_back_to_the_currency_line():
    r = _run("""
    console.log(JSON.stringify({
      ledger: _sheetPurse({ledger: {coins: {pp: 0, gp: 32, ep: 0, sp: 8, cp: 7}, items: []},
                           inventory: ['**Currency:** 1 gp']}),
      empty: _sheetPurse({ledger: {coins: {pp: 0, gp: 0, ep: 0, sp: 0, cp: 0}, items: []}}),
      prose: _sheetPurse({inventory: ['**Weapons:** dagger', '**Currency:** 109 gp. Less 91 gp']}),
      none: _sheetPurse({inventory: ['**Weapons:** dagger']}),
    }));
    """)
    assert r["ledger"] == [["32", "gp"], ["8", "sp"], ["7", "cp"]]
    assert r["empty"] == [["0", "gp"]]
    assert r["prose"] == [["109", "gp"]]
    assert r["none"] is None


def test_items_render_grouped_by_category_with_quantity_equipped_and_notes():
    r = _run("""
    const t = _invTable([
      {name: 'Torch', qty: 10, category: 'consumables', equipped: false},
      {name: 'Longsword', qty: 1, category: 'weapons', equipped: true},
      {name: 'Shield', qty: 1, category: 'armour', equipped: true, notes: 'dented'},
      {name: 'Odd stone', qty: 1, category: 'mystery', equipped: false, weight: 2},
    ]);
    const cargo = _invTable([{name: 'Wagon', qty: 1, category: 'gear', equipped: false,
                             notes: 'rented'}], 'Shared cargo', false);
    console.log(JSON.stringify({
      groups: t.cls('sm-inv-group').map(g => g.textContent),
      names: t.cls('sm-inv-name').map(n => n.textContent),
      qty: t.cls('sm-inv-qty').map(n => n.textContent),
      notes: t.cls('sm-inv-notes').map(n => n.textContent),
      lookup: t.all(c => c.dataset.srdName).map(c => c.dataset.srdName),
      caption: cargo.cls('sm-inv-caption').map(c => c.textContent),
      cargo: cargo.cls('sm-inv-name').map(n => n.textContent),
      cargoGroups: cargo.cls('sm-inv-group').length,
    }));
    """)
    assert r["groups"] == ["Weapons", "Armour", "Consumables", "Other"]
    assert r["names"] == ["Longswordequipped", "Shieldequipped", "Torch", "Odd stone"]
    assert r["qty"] == ["", "", "×10", ""]
    assert r["notes"] == ["", "dented", "", "2 lb"]
    assert r["lookup"] == ["Longsword", "Shield", "Torch", "Odd stone"]
    assert r["caption"] == ["Shared cargo"] and r["cargo"] == ["Wagon"] and r["cargoGroups"] == 0


def test_features_group_by_source_and_open_to_full_text():
    r = _run("""
    const fs = [
      {name: 'Sneak Attack', text: 'Once per turn, extra 1d6.', source: 'Rogue', kind: 'class', kind_label: 'Class'},
      {name: "Thieves' Cant", text: '', source: 'Rogue', kind: 'class', kind_label: 'Class'},
      {name: 'Lucky', tag: 'Origin feat', text: '2 Luck Points.', source: 'Merchant', kind: 'background', kind_label: 'Background'},
      {name: 'Old style', text: 'No source here.'},
    ];
    const groups = _featureGroups(fs);
    const sneak = _featureEl(fs[0]), cant = _featureEl(fs[1]), lucky = _featureEl(fs[2]);
    console.log(JSON.stringify({
      groups: groups.map(g => [g.source, g.label, g.items.map(i => i.name)]),
      sneakTag: sneak.tagName, summary: sneak.children[0].tagName,
      body: sneak.cls('sm-feature-text').map(b => b.textContent),
      rules: sneak.all(c => c.dataset.srdName).map(c => [c.dataset.srdName, c.dataset.srdCategory]),
      cantTag: cant.tagName, cantBody: cant.cls('sm-feature-text').length,
      tag: lucky.cls('sm-feature-tag').map(t => t.textContent),
    }));
    """)
    assert r["groups"] == [["Rogue", "Class", ["Sneak Attack", "Thieves' Cant"]],
                           ["Merchant", "Background", ["Lucky"]], ["", "", ["Old style"]]]
    assert r["sneakTag"] == "DETAILS" and r["summary"] == "SUMMARY"
    assert r["body"] == ["Once per turn, extra 1d6. Rules"]
    assert r["rules"] == [["Sneak Attack", "feature"]]
    assert r["cantTag"] == "DIV" and r["cantBody"] == 0
    assert r["tag"] == ["Origin feat"]


def test_open_sheet_uses_the_ledger_and_keeps_the_old_list_as_fallback():
    src = HTML.read_text(encoding="utf-8")
    body = src[src.index("function openSheet(name)"):]
    body = body[:body.index("\n}\n")]
    assert "const purse = _sheetPurse(sheet);" in body
    assert "if (ledger) {" in body and "} else if (invLines.length) {" in body
    assert "_featureGroups(sheet.features)" in body
    assert body.index("Inventory") < body.index("Features & Passives")


def test_sheet_palette_is_set_for_light_dark_and_editorial():
    css = HTML.read_text(encoding="utf-8")
    for selector in ('#sheet-panel {\n    --sm-ink', ':root[data-theme="light"] #sheet-panel {',
                     ':root:not([data-theme]):not([data-skin="editorial"]) #sheet-panel {',
                     ':root[data-skin="editorial"] #sheet-panel{\n    --sm-ink:var(--e-ink)'):
        assert selector in css, selector
