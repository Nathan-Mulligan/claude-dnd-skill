"""Campaign voice choices stay separate and local previews cannot use paid TTS."""
import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

DISPLAY = Path(__file__).resolve().parents[1] / 'skills/dnd/display'


@pytest.fixture
def display(tmp_path, monkeypatch):
    monkeypatch.setenv('DND_CAMPAIGN_ROOT', str(tmp_path))
    monkeypatch.setenv('DND_RUNTIME_DIR', str(tmp_path / '.runtime'))
    monkeypatch.delenv('DND_CAST_FILE', raising=False)
    monkeypatch.syspath_prepend(str(DISPLAY))
    spec = importlib.util.spec_from_file_location('_campaign_voice_test', DISPLAY / 'dnd-display-app.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.app.testing = True
    monkeypatch.setattr(module, '_broadcast', lambda payload: None)
    monkeypatch.setattr(module, '_voice_ok', lambda voice: voice in ('bf_emma', 'bm_george'))
    for name in ('one', 'two'):
        (tmp_path / 'campaigns' / name).mkdir(parents=True)
    Path(module.CAMP_FILE).write_text('one', encoding='utf-8')
    return module, module.app.test_client()


def test_voice_choice_survives_reload_and_does_not_change_other_campaign(display):
    module, client = display
    assert client.post('/cast', json={'name': 'Nella Reed', 'voice': 'bf_emma'}).status_code == 200
    assert client.get('/cast').json == {'nella reed': 'bf_emma'}
    Path(module.CAMP_FILE).write_text('two', encoding='utf-8')
    assert client.get('/cast').json == {}
    assert client.post('/cast', json={'name': 'Nella Reed', 'voice': 'bm_george'}).status_code == 200
    Path(module.CAMP_FILE).write_text('one', encoding='utf-8')
    assert client.get('/cast').json == {'nella reed': 'bf_emma'}
    assert json.loads(module._cast_file().read_text(encoding='utf-8')) == {'nella reed': 'bf_emma'}


def test_voice_save_failure_is_reported(display, monkeypatch):
    module, client = display
    monkeypatch.setattr(module.os, 'replace', mock.Mock(side_effect=OSError('disk full')))
    assert client.post('/cast', json={'name': 'Nella', 'voice': 'bf_emma'}).status_code == 500


def test_local_preview_rejects_paid_backend(display, monkeypatch):
    module, client = display
    monkeypatch.setattr(module._tts, 'active_backend', lambda: 'gemini')
    with mock.patch.object(module._tts, 'synthesize_strict') as synthesize:
        result = client.post('/tts', json={'text': 'Hello', 'voice': 'bf_emma', 'local_only': True})
        assert result.status_code == 503
        synthesize.assert_not_called()


def test_local_only_synthesis_never_falls_through_to_paid_provider():
    sys.path.insert(0, str(DISPLAY))
    import tts
    with mock.patch.object(tts, 'active_backend', return_value='gemini'), \
         mock.patch.object(tts, '_synthesize_local', return_value=b'\x00\x01') as local, \
         mock.patch.object(tts, '_synthesize_gemini') as paid:
        assert tts.synthesize_strict('Hello', 'bf_emma', local_only=True) == b'\x00\x01'
        local.assert_called_once()
        paid.assert_not_called()


def test_browser_reuses_preloaded_samples_and_skips_paid_catalog():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    source = (DISPLAY / 'templates/index.html').read_text(encoding='utf-8')
    code = source[source.index('const _ttsSamples ='):source.index('function _ttsBuildVoiceMenu')]
    result = subprocess.run([node, '--input-type=module', '-'], text=True, encoding='utf-8', capture_output=True, check=True, input='''
let requests = [];
const _TTS_VOICES_MALE = ['bm_george'], _TTS_VOICES_FEMALE = ['bf_emma'];
function _authHeaders() { return {}; }
async function fetch(url, options) {
  requests.push(JSON.parse(options.body));
  return {ok:true, arrayBuffer:async () => new ArrayBuffer(8)};
}
''' + code + '''
_ttsSampleBackend = 'gemini';
await _ttsPreloadSamples();
if (requests.length) throw new Error('Paid catalog was preloaded');
_ttsSampleBackend = 'local';
await _ttsPreloadSamples();
await Promise.all([_ttsSample('bf_emma'), _ttsSample('bf_emma')]);
console.log(JSON.stringify(requests));
''')
    requests = json.loads(result.stdout)
    assert [r['voice'] for r in requests] == ['bm_george', 'bf_emma']
    assert all(r['local_only'] is True for r in requests)


def test_short_speaker_name_keeps_the_full_character_assignment():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    source = (DISPLAY / 'templates/index.html').read_text(encoding='utf-8')
    code = source[source.index('function _castKey('):source.index('function _voiceForBlock(')]
    result = subprocess.run([node, '-'], text=True, encoding='utf-8', capture_output=True, check=True, input='''
const _CAST = {'nella reed':'bf_emma', 'ann':'af_bella'};
function _narratorVoice() { return 'bm_fable'; }
''' + code + '''
console.log(JSON.stringify([_castKey('Nella'), _voiceForName('Nella Reed'), _voiceForName('Joanna')]));
''')
    assert json.loads(result.stdout) == ['nella reed', 'bf_emma', 'bm_fable']
