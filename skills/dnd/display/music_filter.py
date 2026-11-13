"""Apply the approved 5 kHz roll-off to generated music before publication."""
from pathlib import Path
import wave


def lowpass_wav(source: Path, destination: Path) -> None:
    import numpy as np
    from scipy.signal import butter, sosfilt

    with wave.open(str(source), "rb") as wav:
        channels = wav.getnchannels()
        rate = wav.getframerate()
        if wav.getsampwidth() != 2 or channels not in (1, 2) or rate <= 10000:
            raise ValueError("music filter requires mono/stereo 16-bit PCM above 10 kHz")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").reshape(-1, channels)
    sections = butter(4, 5000, fs=rate, btype="lowpass", output="sos")
    filtered = sosfilt(sections, samples.astype(np.float32), axis=0)
    pcm = np.clip(np.rint(filtered), -32768, 32767).astype("<i2")
    with wave.open(str(destination), "wb") as wav:
        wav.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(pcm.tobytes())
