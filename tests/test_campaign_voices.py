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


# ── Automatic voices for new speakers (issue #84) ──────────────────────────

KOKORO = ['bm_fable', 'bm_george', 'bm_lewis', 'am_fenrir', 'am_onyx',
          'af_heart', 'af_bella', 'bf_alice', 'bf_emma', 'af_sky']


@pytest.fixture
def cast_display(display, monkeypatch):
    module, client = display
    sent = []
    monkeypatch.setattr(module, '_broadcast', sent.append)
    monkeypatch.setattr(module, '_voice_ok', lambda voice: voice in KOKORO)
    monkeypatch.setattr(module, '_read_narrator_voice', lambda: 'bm_fable')
    monkeypatch.setattr(module, '_audio', None)
    return module, client, sent


def _npc(client, name, text='"Well met."', **extra):
    return client.post('/chunk', json={'text': text, 'npc': name, **extra})


def test_picker_is_stable_and_skips_narrator_party_and_used_voices():
    sys.path.insert(0, str(DISPLAY))
    import cast_voices
    first = cast_voices.pick_voice('Garrick Holt', KOKORO, narrator='bm_fable')
    assert first == cast_voices.pick_voice('garrick holt ', KOKORO, narrator='bm_fable')
    assert first != 'bm_fable'
    avoid = {'bm_george', 'af_heart', 'bf_emma'}
    for n in range(40):
        voice = cast_voices.pick_voice(f'Person {n}', KOKORO, narrator='bm_fable',
                                       party=['bm_george'], recent=['af_heart'], used=['bf_emma'])
        assert voice not in avoid | {'bm_fable'}
    # When every voice is taken it still answers, and still not the narrator.
    assert cast_voices.pick_voice('X', KOKORO, narrator='bm_fable', used=KOKORO) != 'bm_fable'
    assert cast_voices.pick_voice('X', [], narrator='bm_fable') == ''


def test_new_npc_line_gets_a_saved_voice_broadcast_before_the_line(cast_display):
    module, client, sent = cast_display
    with module._stats_lock:
        module._current_stats['players'] = [{'name': 'Mira'}]
    client.post('/cast', json={'name': 'Mira', 'voice': 'af_bella'})
    sent.clear()
    assert _npc(client, 'Garrick Holt').status_code == 204
    cast = client.get('/cast').json
    voice = cast['garrick holt']
    assert voice in KOKORO and voice not in ('bm_fable', 'af_bella')
    order = [p for p in sent if 'cast' in p or 'npc' in p]
    assert order[0]['cast']['garrick holt'] == voice
    assert order[1].get('npc') == 'Garrick Holt'
    raw = json.loads(module._cast_file().read_text(encoding='utf-8'))
    assert raw['_sources'] == {'garrick holt': 'auto'}
    # A second line changes nothing and broadcasts no cast.
    sent.clear()
    _npc(client, 'Garrick Holt')
    assert [p for p in sent if 'cast' in p] == []
    assert client.get('/cast').json['garrick holt'] == voice


def test_recent_speakers_voices_are_avoided(cast_display):
    module, client, sent = cast_display
    voices = set()
    for name in ('Ann', 'Bo', 'Cy', 'Di', 'Ed'):
        _npc(client, name)
        voices.add(client.get('/cast').json[name.lower()])
    assert len(voices) == 5 and 'bm_fable' not in voices


def test_fallback_never_overwrites_a_hand_pick(cast_display):
    module, client, sent = cast_display
    client.post('/cast', json={'name': 'Nella Reed', 'voice': 'bf_emma'})
    _npc(client, 'Nella Reed')
    _npc(client, 'Nella')       # short name finds the full entry
    assert client.get('/cast').json == {'nella reed': 'bf_emma'}


def test_dm_voice_is_saved_and_can_replace_an_automatic_pick(cast_display):
    module, client, sent = cast_display
    _npc(client, 'Vesna')
    _npc(client, 'Vesna', npc_voice='bf_alice')
    assert client.get('/cast').json['vesna'] == 'bf_alice'
    raw = json.loads(module._cast_file().read_text(encoding='utf-8'))
    assert raw['_sources']['vesna'] == 'dm'
    _npc(client, 'Oren', npc_voice='am_onyx')
    assert client.get('/cast').json['oren'] == 'am_onyx'
    # An unknown voice id falls back to an automatic pick.
    _npc(client, 'Pell', npc_voice='zz_nobody')
    assert client.get('/cast').json['pell'] in KOKORO


def test_voice_from_the_other_backend_is_replaced_by_a_matching_pick(cast_display, monkeypatch):
    module, client, sent = cast_display
    monkeypatch.setattr(module, '_read_narrator_voice', lambda: 'Enceladus')
    monkeypatch.setattr(module, '_voice_ok', lambda voice: True)
    _npc(client, 'Vesna', npc_voice='bf_alice')
    voice = client.get('/cast').json['vesna']
    assert voice in module._tts.GEMINI_VALID_VOICES and voice != 'Enceladus'


def test_dm_voice_never_replaces_a_hand_pick(cast_display):
    module, client, sent = cast_display
    client.post('/cast', json={'name': 'Nella Reed', 'voice': 'bf_emma'})
    _npc(client, 'Nella', npc_voice='af_sky')
    assert client.get('/cast').json == {'nella reed': 'bf_emma'}
    r = client.post('/cast', json={'name': 'Nella', 'voice': 'af_sky', 'source': 'dm'})
    assert r.status_code == 409 and r.json['voice'] == 'bf_emma'
    # The DM naming the same voice leaves it a hand pick.
    assert client.post('/cast', json={'name': 'Nella', 'voice': 'bf_emma', 'source': 'dm'}).status_code == 200
    assert client.post('/cast', json={'name': 'Nella', 'voice': 'af_sky', 'source': 'dm'}).status_code == 409
    assert client.get('/cast').json == {'nella reed': 'bf_emma'}
    # The menu itself may always change it, and that makes an auto pick manual.
    _npc(client, 'Oren')
    client.post('/cast', json={'name': 'oren', 'voice': 'am_fenrir'})
    _npc(client, 'Oren', npc_voice='bm_lewis')
    assert client.get('/cast').json['oren'] == 'am_fenrir'


def test_older_cast_files_keep_their_comment_and_entries(cast_display):
    module, client, sent = cast_display
    path = module._cast_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'_comment': 'keep me', 'Meren Vale': 'bf_emma'}), encoding='utf-8')
    _npc(client, 'Jessa Pike')
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert raw['_comment'] == 'keep me' and raw['meren vale'] == 'bf_emma'
    assert raw['jessa pike'] not in ('bf_emma', 'bm_fable')


def test_cast_save_failure_does_not_drop_the_line(cast_display, monkeypatch):
    module, client, sent = cast_display
    monkeypatch.setattr(module.os, 'replace', mock.Mock(side_effect=OSError('disk full')))
    assert _npc(client, 'Garrick').status_code == 204
    assert any(p.get('npc') == 'Garrick' for p in sent)


def test_send_npc_voice_flag_goes_on_the_npc_chunk(monkeypatch, capsys):
    sys.path.insert(0, str(DISPLAY))
    import importlib
    import io
    send = importlib.import_module('send')
    posted = []
    monkeypatch.setattr(send, '_post', lambda url, data, token: posted.append(json.loads(data)) or True)
    monkeypatch.setattr(send, '_ensure_active_campaign', lambda: None)
    monkeypatch.setattr(send, '_read_token', lambda: '')
    monkeypatch.setattr(sys, 'stdin', io.StringIO('"Well met."\n'))
    monkeypatch.setattr(sys, 'argv', ['send.py', '--npc', 'Vesna', '--npc-voice', 'bf_alice'])
    send.main()
    assert posted and posted[0]['npc'] == 'Vesna' and posted[0]['npc_voice'] == 'bf_alice'
    for argv in (['send.py', '--npc-voice', 'bf_alice'], ['send.py', '--npc', 'V', '--npc-voice', 'rm -rf']):
        monkeypatch.setattr(sys, 'argv', argv)
        with pytest.raises(SystemExit):
            send.main()


def test_page_takes_cast_updates_from_the_stream():
    source = (DISPLAY / 'templates/index.html').read_text(encoding='utf-8')
    handler = source[source.index('if (payload.cast && typeof payload.cast'):][:200]
    assert '_CAST = payload.cast' in handler
    app = (DISPLAY / 'dnd-display-app.py').read_text(encoding='utf-8')
    stream = app[app.index('def stream():'):app.index('replay_batch', app.index('def stream():'))]
    assert '"cast"' in stream


# ── A DM-chosen voice another speaker already has (issue #129) ─────────────

def _gender(voice):
    sys.path.insert(0, str(DISPLAY))
    import cast_voices
    return cast_voices.voice_gender(voice)


def _canon(voice):
    return '+'.join(sorted(voice.split('+')))


def test_a_new_npc_given_a_companions_voice_gets_a_free_one_of_the_same_gender(cast_display):
    module, client, sent = cast_display
    with module._stats_lock:
        module._current_stats['players'] = [{'name': 'Corin Ash'}]
    client.post('/cast', json={'name': 'Corin Ash', 'voice': 'bm_george'})
    client.post('/cast', json={'name': 'Tamsin Reeve', 'voice': 'bf_alice'})
    for name, asked in (('Ilse Marrow', 'bf_alice'), ('Garrow Pike', 'bm_george')):
        r = _npc(client, name, npc_voice=asked)
        cast = client.get('/cast').json
        voice = cast[name.lower()]
        assert voice != asked and _gender(voice) == _gender(asked)
        assert voice not in ('bm_fable',) and len({_canon(v) for v in cast.values()}) == len(cast)
        note = r.json['note']
        assert name in note and asked in note and voice in note
    # The people who had those voices keep them, hand picks still.
    cast = client.get('/cast').json
    assert cast['corin ash'] == 'bm_george' and cast['tamsin reeve'] == 'bf_alice'
    raw = json.loads(module._cast_file().read_text(encoding='utf-8'))
    assert 'corin ash' not in raw['_sources'] and 'tamsin reeve' not in raw['_sources']
    # The new voice reached the page before the line.
    order = [p for p in sent if 'cast' in p or 'npc' in p]
    assert order[-2]['cast']['garrow pike'] == cast['garrow pike']


def test_a_new_npc_given_the_narrators_voice_gets_another(cast_display):
    module, client, sent = cast_display
    r = _npc(client, 'Old Wendel', npc_voice='bm_fable')
    voice = client.get('/cast').json['old wendel']
    assert voice != 'bm_fable' and _gender(voice) == 'm'
    assert 'narrator' in r.json['note']


def test_a_free_dm_voice_is_kept_and_says_nothing(cast_display):
    module, client, sent = cast_display
    client.post('/cast', json={'name': 'Tamsin Reeve', 'voice': 'bf_alice'})
    assert _npc(client, 'Ilse Marrow', npc_voice='bf_emma').status_code == 204
    assert client.get('/cast').json['ilse marrow'] == 'bf_emma'


def test_a_blend_spelled_the_other_way_is_the_same_voice(cast_display, monkeypatch):
    module, client, sent = cast_display
    monkeypatch.setattr(module, '_voice_ok', lambda voice: all(p in KOKORO for p in voice.split('+')))
    client.post('/cast', json={'name': 'Tamsin Reeve', 'voice': 'bf_alice+bf_emma'})
    _npc(client, 'Ilse Marrow', npc_voice='bf_emma+bf_alice')
    assert _canon(client.get('/cast').json['ilse marrow']) != 'bf_alice+bf_emma'


def test_an_existing_npc_is_never_moved_onto_someone_elses_voice(cast_display):
    module, client, sent = cast_display
    client.post('/cast', json={'name': 'Tamsin Reeve', 'voice': 'bf_alice'})
    _npc(client, 'Ilse Marrow', npc_voice='bf_emma')
    # Repeating a clashing voice= on later lines changes nobody.
    for _ in range(2):
        r = _npc(client, 'Ilse Marrow', npc_voice='bf_alice')
        assert 'bf_emma' in r.json['note']
    assert client.get('/cast').json == {'tamsin reeve': 'bf_alice', 'ilse marrow': 'bf_emma'}
    # The second line of a speaker cast in place of a clash keeps the same voice and note.
    first = _npc(client, 'Garrow Pike', npc_voice='bf_alice').json['note']
    voice = client.get('/cast').json['garrow pike']
    assert _npc(client, 'Garrow Pike', npc_voice='bf_alice').json['note'] == first
    assert client.get('/cast').json['garrow pike'] == voice


def test_when_no_voice_of_that_gender_is_free_the_dm_is_told(cast_display, monkeypatch):
    module, client, sent = cast_display
    monkeypatch.setattr(module, '_cast_voice_genders', lambda narrator: (['bm_fable', 'bm_george'], []))
    client.post('/cast', json={'name': 'Tamsin Reeve', 'voice': 'bm_george'})
    r = _npc(client, 'Garrow Pike', npc_voice='bm_george')
    assert client.get('/cast').json['garrow pike'] == 'bm_george'
    assert 'no free voice' in r.json['note']


def test_send_prints_the_displays_note_once(monkeypatch, capsys):
    sys.path.insert(0, str(DISPLAY))
    import importlib
    send = importlib.import_module('send')
    note = "Ilse Marrow speaks as bf_emma: bf_alice is already tamsin reeve's voice."

    class Reply:
        status = 200
        def read(self, n=-1):
            return json.dumps({'note': note}).encode('utf-8')

    monkeypatch.setattr(send.urllib.request, 'urlopen', lambda *a, **k: Reply())
    monkeypatch.setattr(send, '_NOTES', set())
    assert send._post('http://display/chunk', b'{}', '') is True
    assert send._post('http://display/chunk', b'{}', '') is True
    err = capsys.readouterr().err
    assert err.count(note) == 1 and err.startswith('send.py: ')
