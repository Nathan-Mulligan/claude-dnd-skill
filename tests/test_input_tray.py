"""The bottom tray can collapse without losing the draft or opening history."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

HTML = Path(__file__).resolve().parents[1] / 'skills/dnd/display/templates/index.html'


def test_tray_keyboard_toggle_remembers_choice():
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    source = HTML.read_text()
    code = source[source.index('function _setInputExpanded('):source.index('// ── Build character tabs')]
    result = subprocess.run([node, '-'], text=True, capture_output=True, check=True, input='''
const classes = new Set(['collapsed']);
const listeners = {}, attributes = {}, saved = new Map();
const _inputPanel = {classList:{contains:key=>classes.has(key), toggle(key,on){on?classes.add(key):classes.delete(key);}}};
const _inputPanelHdr = {setAttribute:(key,value)=>attributes[key]=value, addEventListener:(key,handler)=>listeners[key]=handler};
const _inputArrow = {};
const localStorage = {getItem:key=>saved.get(key), setItem:(key,value)=>saved.set(key,value)};
let reservations = 0;
function _reserveInputSpace() { reservations++; }
''' + code + '''
listeners.keydown({key:'Enter',preventDefault(){}});
const collapsed = classes.has('collapsed') && attributes['aria-expanded']==='false';
listeners.keydown({key:' ',preventDefault(){}});
console.log(JSON.stringify({collapsed, expanded:!classes.has('collapsed'), remembered:saved.get('dnd_input_expanded'), reservations}));
''')
    result = json.loads(result.stdout)
    assert result == {'collapsed': True, 'expanded': True, 'remembered': '1', 'reservations': 3}


def test_reply_history_is_native_collapsible_and_closed_by_default():
    from html.parser import HTMLParser

    class HistoryParser(HTMLParser):
        history = None
        summary = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get('id') == 'party-response-history':
                self.history = (tag, attrs)
            if attrs.get('id') == 'party-response-summary':
                self.summary = tag

    parser = HistoryParser()
    parser.feed(HTML.read_text())
    tag, attrs = parser.history
    assert tag == 'details'
    assert 'open' not in attrs
    assert parser.summary == 'summary'
