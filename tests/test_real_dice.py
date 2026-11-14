"""Real dice: the table can roll physical dice and type the faces into the display."""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DISPLAY = Path(__file__).resolve().parents[1] / 'skills/dnd/display'
HTML = DISPLAY / 'templates/index.html'


@pytest.fixture
def display(tmp_path, monkeypatch):
    monkeypatch.setenv('DND_CAMPAIGN_ROOT', str(tmp_path))
    monkeypatch.setenv('DND_RUNTIME_DIR', str(tmp_path / '.runtime'))
    monkeypatch.syspath_prepend(str(DISPLAY))
    spec = importlib.util.spec_from_file_location('_real_dice_test', DISPLAY / 'dnd-display-app.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.app.testing = True
    sent = []
    monkeypatch.setattr(module, '_broadcast', sent.append)
    for name in ('one', 'two'):
        folder = tmp_path / 'campaigns' / name
        folder.mkdir(parents=True)
        (folder / 'state.md').write_text('# State\n\n## Session Flags\nroll_mode: players\n', encoding='utf-8')
    Path(module.CAMP_FILE).write_text('one', encoding='utf-8')
    module._dice_pending.clear()
    module._dice_finished.clear()
    return module, module.app.test_client(), sent, tmp_path


def _state(tmp_path, name):
    return (tmp_path / 'campaigns' / name / 'state.md').read_text(encoding='utf-8')


def test_dice_input_defaults_to_click_to_roll(display):
    _, client, _, _ = display
    assert client.get('/dice-input').json == {'mode': 'click'}
    assert '<meta name="dice-input" content="click">' in client.get('/').get_data(as_text=True)


def test_dice_input_is_saved_with_the_campaign_and_shared(display):
    module, client, sent, tmp_path = display
    reply = client.post('/dice-input', json={'mode': 'real'})
    assert reply.status_code == 200
    assert reply.json == {'mode': 'real', 'persisted': True}
    assert {'dice_input': 'real'} in sent
    assert 'dice_input: real' in _state(tmp_path, 'one')
    assert 'roll_mode: players' in _state(tmp_path, 'one')
    assert client.get('/dice-input').json == {'mode': 'real'}
    assert '<meta name="dice-input" content="real">' in client.get('/').get_data(as_text=True)

    Path(module.CAMP_FILE).write_text('two', encoding='utf-8')
    assert client.get('/dice-input').json == {'mode': 'click'}
    assert 'dice_input' not in _state(tmp_path, 'two')

    Path(module.CAMP_FILE).write_text('one', encoding='utf-8')
    assert client.post('/dice-input', json={'mode': 'click'}).json == {'mode': 'click', 'persisted': True}
    assert _state(tmp_path, 'one').count('dice_input:') == 1
    assert client.get('/dice-input').json == {'mode': 'click'}


@pytest.mark.parametrize('body', [{'mode': 'phone'}, {'mode': ''}, {}, {'mode': ['real']}])
def test_dice_input_rejects_unknown_modes(display, body):
    _, client, sent, tmp_path = display
    assert client.post('/dice-input', json=body).status_code == 400
    assert 'dice_input' not in _state(tmp_path, 'one')
    assert sent == []


def test_campaign_switch_tells_displays_its_dice_input(display):
    module, client, sent, tmp_path = display
    state = tmp_path / 'campaigns' / 'two' / 'state.md'
    state.write_text(state.read_text(encoding='utf-8') + 'dice_input: real\n', encoding='utf-8')
    client.post('/stats', json={'campaign': 'two'})
    assert any(p.get('dice_input') == 'real' for p in sent if isinstance(p, dict))


def _roll(client, **body):
    body.setdefault('character', 'Elbane')
    return client.post('/player-input/dice', json=body)


def test_real_roll_adds_the_modifier_and_says_it_was_rolled_at_the_table(display):
    _, client, sent, _ = display
    reply = _roll(client, spec='1d20', modifier=3, label='Stealth', faces=[14])
    assert reply.status_code == 200
    data = reply.json
    assert data['rolls'] == [14] and data['kept'] == [14] and data['total'] == 17
    assert data['manual'] is True
    assert data['text'] == 'Elbane rolls 1d20+3: [14] +3 = 17 — Stealth (rolled at the table)'
    assert {'text': data['text'], 'dice': True} in sent


@pytest.mark.parametrize('adv, faces, kept, total', [
    ('advantage', [7, 15], 15, 17),
    ('disadvantage', [7, 15], 7, 9),
])
def test_real_roll_with_advantage_takes_both_dice(display, adv, faces, kept, total):
    _, client, _, _ = display
    data = _roll(client, spec='1d20', modifier=2, advantage=adv, faces=faces).json
    assert data['both'] == faces and data['kept'] == [kept] and data['total'] == total
    assert data['text'] == f'Elbane rolls 1d20+2: [7, 15] → keep {kept} ({adv}) +2 = {total} (rolled at the table)'


def test_real_roll_of_two_dice_sums_each_face(display):
    _, client, _, _ = display
    data = _roll(client, spec='2d6', faces=[3, 5]).json
    assert data['rolls'] == [3, 5] and data['subtotal'] == 8 and data['total'] == 8
    assert data['text'] == 'Elbane rolls 2d6: [3, 5] = 8 (rolled at the table)'


@pytest.mark.parametrize('spec, adv, faces', [
    ('1d20', 'normal', [21]),
    ('1d20', 'normal', [0]),
    ('1d20', 'normal', []),
    ('1d20', 'normal', [4, 5]),
    ('1d20', 'advantage', [12]),
    ('1d20', 'disadvantage', [12, 25]),
    ('2d6', 'normal', [3]),
    ('2d6', 'normal', [3, 7]),
    ('1d100', 'normal', [101]),
    ('1d20', 'normal', ['12']),
    ('1d20', 'normal', [12.5]),
    ('1d20', 'normal', [True]),
    ('1d20', 'normal', 12),
])
def test_real_roll_rejects_faces_the_dice_cannot_show(display, spec, adv, faces):
    module, client, sent, _ = display
    request_id = client.post('/dice-request', json={'character': 'Elbane', 'spec': spec, 'advantage': adv}).json['request_id']
    sent.clear()
    reply = _roll(client, spec=spec, advantage=adv, faces=faces, request_id=request_id)
    assert reply.status_code == 400
    assert sent == []
    assert client.get(f'/dice-request/{request_id}').json['complete'] is False


def test_real_roll_releases_the_dm_wait_with_the_same_line(display):
    _, client, _, _ = display
    request_id = client.post('/dice-request', json={
        'characters': ['Elbane', 'Nella'], 'spec': '1d20', 'modifier': 4, 'dc': 13, 'label': 'Persuasion',
    }).json['request_id']
    first = _roll(client, spec='1d20', modifier=4, label='Persuasion', faces=[9], request_id=request_id).json
    assert client.get(f'/dice-request/{request_id}').json['pending'] == ['Nella']
    second = _roll(client, character='Nella', spec='1d20', modifier=4, label='Persuasion', request_id=request_id).json
    status = client.get(f'/dice-request/{request_id}').json
    assert status['complete'] is True
    assert status['results'] == [first['text'], second['text']]
    assert first['text'].endswith('(rolled at the table)')
    assert 'rolled at the table' not in second['text']


def test_click_to_roll_is_unchanged(display):
    _, client, _, _ = display
    data = _roll(client, spec='2d6', modifier=1).json
    assert len(data['rolls']) == 2 and all(1 <= r <= 6 for r in data['rolls'])
    assert data['total'] == sum(data['rolls']) + 1
    assert 'manual' not in data
    assert data['text'] == f"Elbane rolls 2d6+1: [{data['rolls'][0]}, {data['rolls'][1]}] +1 = {data['total']}"


NODE = shutil.which('node')


@pytest.mark.skipif(not NODE, reason='node not available')
def test_pad_asks_for_one_face_per_physical_die():
    source = HTML.read_text(encoding='utf-8')
    code = source[source.index('function _realDiceSlots('):source.index('// ── Phone dice pad')]
    result = subprocess.run([NODE, '--input-type=module', '-e', code + '''
console.log(JSON.stringify([
  _realDiceSlots('1d20', 'normal'), _realDiceSlots('1d20', 'advantage'),
  _realDiceSlots('1d20', 'disadvantage'), _realDiceSlots('2d6', 'normal'),
  _realDiceSlots('1d100', 'normal'), _realDiceSlots('1d8', 'advantage'),
]));
'''], capture_output=True, text=True, encoding='utf-8', check=True)
    assert json.loads(result.stdout) == [[20], [20, 20], [20, 20], [6, 6], [100], [8]]
