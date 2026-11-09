"""The approved music wording and published audio processing."""
import math
import sys
import wave
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/dnd/display'))
import music

AMBIENCE = ('Atmospheric medieval instrumental background music with sparse frame drum. '
            'Spacious and understated, long spaces between melodies.')
NEGATIVE = 'Vocals, distortion, guitar, lute, drone.'

@pytest.mark.parametrize('setting,mood,prefix', [
    ('arcane','curious','Arcane tower fantasy music. Measured, intricate and subtly strange.'),
    ('ruins','curious','Ancient ruins fantasy music. Weathered stone, forgotten history and quiet mystery.'),
    ('tavern','warm','Medieval tavern fantasy music. A friendly but aged roadside inn.'),
    ('cave','ominous','Gloomy cavern fantasy music. Low, hollow and shadowed, with slow movement.'),
])
def test_approved_ambience(setting, mood, prefix):
    assert music.prompt_for(setting,mood,'bed') == prefix + ' ' + AMBIENCE

@pytest.mark.parametrize('kind', ['intro','bed','combat'])
def test_job_settings(kind, tmp_path):
    director=music.MusicDirector(tmp_path,None,None,autostart=False)
    director.transition('scene','test','tavern','warm')
    job=director._make_job(kind)
    assert job.cfg == 2
    assert job.seconds == (120 if kind == 'bed' else 10)


def test_battle_is_distinct_from_tension():
    battle=music.prompt_for('tavern','warm','combat')
    assert battle == ('Medieval fantasy battle music with frame drum. Driving rhythm, '
                      'forceful bowed string phrases. Urgent and determined.')
    intro=music.prompt_for('city','tense','intro')
    assert 'approaching danger' in intro
    assert 'resolved' not in intro
    assert 'horn' not in battle


def test_context_era_and_inherited_places_remain_in_prompt():
    scene=music.SceneContext.parse({'key':'tower','label':'Workshop','description':'Clockwork instruments fill the room.',
                                  'era':'industrial','theme':[{'name':'Old City'}]})
    prompt=music.prompt_for('arcane','curious','bed',scene)
    assert 'Atmospheric industrial instrumental' in prompt
    assert 'medieval' not in prompt
    assert scene.description in prompt and scene.label in prompt and 'Old City' in prompt


def test_lowpass_preserves_format_and_attenuates_high_frequencies(tmp_path):
    from music_filter import lowpass_wav
    rate=44100
    time=np.arange(rate)/rate
    samples=np.column_stack([np.sin(2*math.pi*1000*time),np.sin(2*math.pi*12000*time)])*.4
    source=tmp_path/'source.wav';target=tmp_path/'filtered.wav'
    with wave.open(str(source),'wb') as wav:
        wav.setparams((2,2,rate,0,'NONE','not compressed'))
        wav.writeframes((samples*32767).astype('<i2').tobytes())
    lowpass_wav(source,target)
    with wave.open(str(target),'rb') as wav:
        assert (wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),wav.getnframes()) == (2,2,rate,rate)
        result=np.frombuffer(wav.readframes(rate),dtype='<i2').reshape(-1,2)/32767
    gain=np.std(result[1000:],axis=0)/np.std(samples[1000:],axis=0)
    assert gain[0] > .98
    assert 20*np.log10(gain[1]) < -30


@pytest.mark.parametrize('kind',['intro','bed','combat'])
def test_renderer_applies_negative_prompt_and_filter_before_publish(tmp_path,kind):
    job=music.Job(1,'entry',kind,'test prompt',10,100,2.0,tmp_path/'result.wav')
    def render(cmd,**kwargs):
        assert cmd[cmd.index('--negative-prompt')+1] == NEGATIVE
        assert cmd[cmd.index('--steps')+1] == '8'
        assert cmd[cmd.index('--cfg')+1] == '2.0'
        with wave.open(cmd[cmd.index('--out')+1],'wb') as wav:
            wav.setparams((2,2,44100,0,'NONE','not compressed'))
            wav.writeframes(b'\0'*1764)
    with mock.patch.object(music.subprocess,'run',side_effect=render), mock.patch.object(music,'lowpass_wav',wraps=music.lowpass_wav) as filter_call:
        music.render_cli(job,Path('/fake/sa3'),None)
        filter_call.assert_called_once()
    assert job.path.is_file()
    assert list(tmp_path.iterdir()) == [job.path]


def test_filter_failure_does_not_publish_unfiltered_audio(tmp_path):
    job=music.Job(1,'entry','intro','prompt',10,100,2.0,tmp_path/'result.wav')
    def render(cmd,**kwargs):
        Path(cmd[cmd.index('--out')+1]).write_bytes(b'x'*100)
    with mock.patch.object(music.subprocess,'run',side_effect=render):
        with pytest.raises((wave.Error,EOFError)):
            music.render_cli(job,Path('/fake/sa3'),None)
    assert not list(tmp_path.iterdir())
