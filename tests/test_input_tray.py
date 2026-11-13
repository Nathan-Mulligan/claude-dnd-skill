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
    source = HTML.read_text(encoding="utf-8")
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
    parser.feed(HTML.read_text(encoding="utf-8"))
    tag, attrs = parser.history
    assert tag == 'details'
    assert 'open' not in attrs
    assert parser.summary == 'summary'


def test_tray_follows_each_frame_of_a_sideways_column_transition():
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    source = HTML.read_text(encoding="utf-8")
    code = source[source.index('function _reserveInputSpace()'):source.index('// Initialise with no extra chars')]
    result = subprocess.run([node, '-'], text=True, capture_output=True, check=True, input='''
let columnLeft = 100;
const frames = [], listeners = {};
const panel = {style:{},getBoundingClientRect:()=>({height:200,top:600}),addEventListener(){}};
const scroll = {style:{},addEventListener:(name,handler)=>listeners[name]=handler};
const content = {getBoundingClientRect:()=>({left:columnLeft,width:400})};
const document = {body:{classList:{contains:()=>false}},getElementById:id=>({'input-panel':panel,'text-scroll':scroll,'text-content':content}[id])};
const window = {innerHeight:800,addEventListener(){}};
class MutationObserver {observe(){}}
function requestAnimationFrame(callback) { frames.push(callback); return frames.length; }
function cancelAnimationFrame() {}
''' + code + '''
if (listeners.transitionrun) listeners.transitionrun({target:scroll,propertyName:'padding-left'});
columnLeft = 110;
if (frames.length) frames.shift()();
const first = panel.style.left;
columnLeft = 120;
if (frames.length) frames.shift()();
const second = panel.style.left;
if (listeners.transitionend) listeners.transitionend({target:scroll,propertyName:'padding-left'});
console.log(JSON.stringify({first,second,width:panel.style.width}));
''')
    assert json.loads(result.stdout) == {'first': '110px', 'second': '120px', 'width': '400px'}
