"""Browser audio choices survive campaign entry and remain local to each display."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


HTML = Path(__file__).resolve().parents[1] / "skills/dnd/display/templates/index.html"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node not available")


def run_browser_code(code):
    result = subprocess.run([NODE, "--input-type=module", "-e", code],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_campaign_entry_keeps_saved_mute_choices():
    source = HTML.read_text(encoding="utf-8")
    entry = source[source.index("const _sfxTrack ="):source.index("// ── DM Help button", source.index("const _sfxTrack ="))]
    music = source[source.index("const _musicTrack ="):source.index("let _musicVolume =")]
    actual = run_browser_code("""
const saved = new Map(['dnd_sfx_on', 'dnd_music_on', 'dnd_intro_on', 'dnd_tts_auto'].map(key => [key, '0']));
const localStorage = {getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value)};
const window = {location: {search:'?audio=on', pathname:'/', hash:''}};
const location = window.location;
const history = {replaceState() {}};
const document = {getElementById() {return {};}};
function _setTrack() {}
""" + entry + music + """
console.log(JSON.stringify({sfx:_sfxOn, music:_musicOn, saved:Object.fromEntries(saved)}));
""")
    assert actual["sfx"] is False
    assert actual["music"] is False
    assert set(actual["saved"].values()) == {"0"}


def test_muting_one_display_does_not_disable_shared_sound_cues():
    source = HTML.read_text(encoding="utf-8")
    code = source[source.index("function _syncSfx()"):source.index("// ── Typing speed toggle")]
    actual = run_browser_code("""
const requests = [];
const states = [];
let click;
let _sfxOn = true;
const _sfxTrack = {};
const _sfxGain = null;
const _inputOnlyAudio = false;
const localStorage = {setItem() {}};
const document = {getElementById() {return {addEventListener(type, handler) {click = handler;}};}};
function _setTrack(element, on) {states.push(on);}
function fetch(url, options) {requests.push(JSON.parse(options.body)); return Promise.resolve({});}
""" + code + """
click();
click();
console.log(JSON.stringify({requests, states}));
""")
    assert actual["states"] == [True, False, True]
    assert actual["requests"] == [{"sfx": True}]


def test_narration_volume_controls_existing_and_future_playback():
    source = HTML.read_text(encoding='utf-8')
    state = source[source.index('let _ttsCtx = null;'):source.index('// Free fallback:')]
    context = source[source.index('function _getTtsCtx()'):source.index('const _SVG_SPEAKER')]
    schedule = source[source.index('function _ttsSchedule('):source.index('function _ttsWaitForQueue(')]
    actual = run_browser_code('''
const saved = new Map();
const localStorage = {setItem: (key, value) => saved.set(key, value)};
const _storedVolume = () => 0.6;
const connections = [], levels = [];
class AudioContext {
  constructor() {this.state = 'running'; this.currentTime = 2; this.destination = {};}
  createGain() {return {gain:{value:1, setTargetAtTime(value) {levels.push(value);}}, connect() {}};}
  createBuffer() {return {getChannelData: () => new Float32Array(2), duration:0.01};}
  createBufferSource() {return {connect(target) {connections.push(target);}, start() {}};}
}
const window = {AudioContext};
let _ttsNextAt = 0;
const _ttsSources = new Set(), _TTS_LINE_PAUSE = 0.1;
''' + state + context + schedule + '''
_setNarrationVolume(0.25);
const ctx = _getTtsCtx();
const initial = _ttsGain.gain.value;
_ttsSchedule(ctx, new Int16Array([1, 2]).buffer, null, true, true, 0);
_setNarrationVolume(0);
const routedThroughVolume = connections[0] === _ttsGain;
ctx.state = 'closed';
_getTtsCtx();
console.log(JSON.stringify({initial, levels, routedThroughVolume, recreated:_ttsGain.gain.value, saved:Object.fromEntries(saved)}));
''')
    assert actual == {'initial': 0.25, 'levels': [0], 'routedThroughVolume': True,
                      'recreated': 0, 'saved': {'dnd_narration_volume': '0'}}


def test_browser_speech_fallback_uses_narration_volume():
    source = HTML.read_text(encoding='utf-8')
    speak = source[source.index('function _ttsBrowserSay('):source.index('let _ttsUnlockHinted')]
    actual = run_browser_code('''
const _TTS_HAS_BROWSER = true, _ttsVolume = 0.37;
const _ttsPickBrowserVoice = () => null;
const _ttsChunkText = () => ['First sentence.', 'Second sentence.'];
const volumes = [];
class SpeechSynthesisUtterance {constructor(text) {this.text = text;}}
const speechSynthesis = {speak(u) {volumes.push(u.volume); if(u.onend) u.onend();}};
''' + speak + '''
await _ttsBrowserSay('Two sentences.');
console.log(JSON.stringify(volumes));
''')
    assert actual == [0.37, 0.37]
