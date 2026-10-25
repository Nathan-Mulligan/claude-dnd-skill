"""Narrator TTS with two interchangeable backends.

  local   — any OpenAI-compatible /v1/audio/speech server (Kokoro, Piper,
            LocalAI…). No API key, no per-character cost, runs offline.
  gemini  — Gemini Flash TTS via a Google AI Studio API key.

Server-side wrapper called by /tts in dnd-display-app.py. Both backends return
raw L16 PCM (24 kHz mono), so the caller cannot tell them apart.

Backend selection (DND_TTS_BACKEND: auto | local | gemini; default auto):
  auto    — use the local server when it answers, else Gemini when a key is
            configured, else report "unset" and degrade to text-only narration.

Local endpoint resolves from DND_TTS_LOCAL_URL env var →
~/.config/claude-dnd/tts.url → http://127.0.0.1:8880. The Gemini key resolves
from DND_TTS_KEY env var → GEMINI_API_KEY env var → ~/.config/claude-dnd/tts.key.

Every failure path returns None from synthesize() so narration silently falls
back to text.

stdlib only — no requests, no google-cloud-sdk, no dependencies.
Setup walkthrough: docs/SKILL-tts.md.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# ── Configuration ───────────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "claude-dnd"

GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_TTS_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_TTS_MODEL}:generateContent"
)

KEY_FILE = CONFIG_DIR / "tts.key"

# Curated 9-voice catalog. The full Gemini API exposes 30 voices; this
# subset keeps the per-block dropdown scannable. Extend VALID_VOICES and
# the matching arrays in display/templates/index.html if you want the
# full catalog.
GEMINI_VOICES_MALE = ("Charon", "Enceladus", "Fenrir", "Umbriel")
GEMINI_VOICES_FEMALE = ("Aoede", "Gacrux", "Kore", "Vindemiatrix", "Zephyr")
GEMINI_VALID_VOICES = frozenset(GEMINI_VOICES_MALE + GEMINI_VOICES_FEMALE)
GEMINI_DEFAULT_VOICE = "Enceladus"

# ── Local backend ───────────────────────────────────────────────────────────

URL_FILE = CONFIG_DIR / "tts.url"
LOCAL_DEFAULT_URL = "http://127.0.0.1:8880"

# Curated to match the Gemini catalog's size so the dropdown stays scannable.
# Kokoro ships ~67; list them all with `python3 tts.py --list-voices`, and use
# any name the server accepts — validation is delegated to the server, not to
# this tuple. Kokoro also blends voices with "+" (e.g. "bm_lewis+am_fenrir").
LOCAL_VOICES_MALE = ("bm_fable", "bm_george", "bm_lewis", "am_fenrir", "am_onyx")
LOCAL_VOICES_FEMALE = ("af_heart", "af_bella", "bf_alice", "bf_emma", "af_sky")
LOCAL_VALID_VOICES = frozenset(LOCAL_VOICES_MALE + LOCAL_VOICES_FEMALE)
LOCAL_DEFAULT_VOICE = "bm_fable"          # deep and measured — the Enceladus analogue

# Reachability is probed rather than configured, so a stopped container simply
# falls through to Gemini. Cached briefly to keep per-block synthesis from
# re-probing on every call.
_PROBE_TTL = 30.0
_probe_cache: tuple[float, bool] | None = None

# Gemini Flash TTS responses degrade in latency past this point and may
# truncate. Bigger blocks should be chunked by the caller; the narration
# path naturally chunks at block flush.
MAX_TEXT_CHARS = 2000

# Request timeout — Gemini Flash TTS typical latency is 1-3s; we give it
# substantial headroom for cold-start or transient slowness.
DEFAULT_TIMEOUT = 30.0


# ── Key resolution ──────────────────────────────────────────────────────────

def _get_api_key() -> Optional[str]:
    """Resolve the Gemini API key. Env vars take precedence over the key file."""
    for env in ("DND_TTS_KEY", "GEMINI_API_KEY"):
        v = os.environ.get(env)
        if v and v.strip():
            return v.strip()
    if KEY_FILE.exists():
        try:
            return KEY_FILE.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return None


# ── Backend resolution ──────────────────────────────────────────────────────

def local_url() -> str:
    """Resolve the local TTS base URL. Env var takes precedence over the file."""
    v = os.environ.get("DND_TTS_LOCAL_URL")
    if v and v.strip():
        return v.strip().rstrip("/")
    if URL_FILE.exists():
        try:
            stored = URL_FILE.read_text(encoding="utf-8").strip()
            if stored:
                return stored.rstrip("/")
        except OSError:
            pass
    return LOCAL_DEFAULT_URL


def local_available(force: bool = False) -> bool:
    """Return True when the local server answers, cached for _PROBE_TTL seconds."""
    global _probe_cache
    now = time.monotonic()
    # A miss is re-checked after a few seconds: Kokoro answering slowly while
    # it synthesises must not read as "down" for half a minute.
    if not force and _probe_cache and now - _probe_cache[0] < (_PROBE_TTL if _probe_cache[1] else 5):
        return _probe_cache[1]
    try:
        with urllib.request.urlopen(f"{local_url()}/v1/audio/voices", timeout=2.0) as r:
            ok = r.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        ok = False
    _probe_cache = (now, ok)
    return ok


def active_backend() -> Optional[str]:
    """Return 'local', 'gemini', or None when neither is usable.

    An explicit DND_TTS_BACKEND is honored even if that backend is down, so a
    misconfiguration surfaces as a clear error rather than a silent failover.
    """
    choice = (os.environ.get("DND_TTS_BACKEND") or "auto").strip().lower()
    if choice in ("local", "gemini"):
        return choice
    if local_available():
        return "local"
    if _get_api_key():
        return "gemini"
    return None


def voice_catalog() -> dict:
    """Return the active backend's voice lists, for the dropdown and /tts/voices."""
    if active_backend() == "local":
        return {
            "backend": "local",
            "male": list(LOCAL_VOICES_MALE),
            "female": list(LOCAL_VOICES_FEMALE),
            "default": LOCAL_DEFAULT_VOICE,
        }
    return {
        "backend": "gemini",
        "male": list(GEMINI_VOICES_MALE),
        "female": list(GEMINI_VOICES_FEMALE),
        "default": GEMINI_DEFAULT_VOICE,
    }


def __getattr__(name: str):
    """Resolve VALID_VOICES / DEFAULT_VOICE against the active backend (PEP 562).

    These stay module-level names so existing callers — `_tts.VALID_VOICES` and
    `_tts.DEFAULT_VOICE` in dnd-display-app.py — keep working unchanged. They
    cannot be plain constants because the right value depends on which backend
    is live, and probing for that at import time would put a network call in
    the import path.
    """
    if name == "VALID_VOICES":
        return (LOCAL_VALID_VOICES if active_backend() == "local"
                else GEMINI_VALID_VOICES)
    if name == "DEFAULT_VOICE":
        return (LOCAL_DEFAULT_VOICE if active_backend() == "local"
                else GEMINI_DEFAULT_VOICE)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def key_source() -> str:
    """Return a human description of the active backend, for diagnostics.

    Used by the verify path and the /dnd load surface — never returns the key.
    Callers treat the literal "unset" as "TTS is not configured".
    """
    backend = active_backend()
    if backend == "local":
        return f"local:{local_url()}"
    if backend == "gemini":
        for env in ("DND_TTS_KEY", "GEMINI_API_KEY"):
            v = os.environ.get(env)
            if v and v.strip():
                return f"env:{env}"
        if KEY_FILE.exists() and KEY_FILE.read_text(encoding="utf-8").strip():
            return f"file:{KEY_FILE}"
    return "unset"


# ── Synthesis ───────────────────────────────────────────────────────────────

class TtsError(Exception):
    """Raised by synthesize_strict; caught by synthesize to fail silently."""


def synthesize_strict(
    text: str,
    voice: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> bytes:
    """Synthesize via the active backend, returning raw L16 PCM (24 kHz mono).

    Raises TtsError for any failure. Use synthesize() for the silent-fail path.

    `voice` defaults to the active backend's default. Passing a voice the
    backend doesn't know falls back to that default rather than erroring —
    matching how the display treats an unknown voice from localStorage.
    """
    text = (text or "").strip()
    if not text:
        raise TtsError("empty text")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]

    backend = active_backend()
    if backend is None:
        raise TtsError("no backend available (no local server, no api key)")

    if backend == "local":
        voice = voice or LOCAL_DEFAULT_VOICE
        return _synthesize_local(text, voice, timeout)

    voice = voice or GEMINI_DEFAULT_VOICE
    return _synthesize_gemini(text, voice, timeout)


def _synthesize_local(text: str, voice: str, timeout: float) -> bytes:
    """Call an OpenAI-compatible /v1/audio/speech server for raw PCM.

    Voice names are passed through untouched: the server owns its own catalog,
    and Kokoro in particular accepts blends ("bm_lewis+am_fenrir") that no
    fixed allow-list here could anticipate.
    """
    body = json.dumps({
        "model": os.environ.get("DND_TTS_LOCAL_MODEL", "kokoro"),
        "voice": voice,
        "input": text,
        "response_format": "pcm",
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{local_url()}/v1/audio/speech",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise TtsError(f"http {resp.status}")
            pcm = resp.read()
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise TtsError(f"http {e.code}: {snippet}") from e
    except urllib.error.URLError as e:
        raise TtsError(f"network: {e.reason}") from e
    except Exception as e:
        raise TtsError(f"unexpected: {e}") from e

    if not pcm:
        raise TtsError("empty pcm payload")
    return pcm


def _synthesize_gemini(text: str, voice: str, timeout: float) -> bytes:
    """Call Gemini Flash TTS and return raw L16 PCM bytes (24 kHz mono)."""
    key = _get_api_key()
    if not key:
        raise TtsError("no api key configured")

    if voice not in GEMINI_VALID_VOICES:
        voice = GEMINI_DEFAULT_VOICE

    body = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice}
                }
            },
        },
    }
    body_bytes = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        f"{GEMINI_TTS_URL}?key={key}",
        data=body_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise TtsError(f"http {resp.status}")
            raw = resp.read()
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise TtsError(f"http {e.code}: {snippet}") from e
    except urllib.error.URLError as e:
        raise TtsError(f"network: {e.reason}") from e
    except Exception as e:
        raise TtsError(f"unexpected: {e}") from e

    try:
        data = json.loads(raw)
        b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise TtsError(f"bad response shape: {e}") from e

    try:
        pcm = base64.b64decode(b64)
    except Exception as e:
        raise TtsError(f"bad base64: {e}") from e

    if not pcm:
        raise TtsError("empty pcm payload")
    return pcm


def synthesize(text: str, voice: Optional[str] = None) -> Optional[bytes]:
    """Silent-fail wrapper. Returns L16 PCM bytes or None on any failure."""
    try:
        return synthesize_strict(text, voice)
    except TtsError:
        return None


# ── CLI for verification ────────────────────────────────────────────────────

def _cli() -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        description="Verify TTS setup for the DnD display companion.",
    )
    p.add_argument(
        "--test", action="store_true",
        help="Check backend + run a synthesis call. Writes nothing.",
    )
    p.add_argument(
        "--speak", action="store_true",
        help="Also play the synthesized audio via afplay (macOS).",
    )
    p.add_argument(
        "--text", default="Hello, narrator voice test. The torchlit hall awaits.",
        help="Override the default test phrase.",
    )
    p.add_argument(
        "--voice", default=None,
        help="Voice name (default: the active backend's default voice).",
    )
    p.add_argument(
        "--list-voices", action="store_true",
        help="List every voice the local server offers, then exit.",
    )
    args = p.parse_args()

    if args.list_voices:
        if not local_available(force=True):
            print(f"No local TTS server at {local_url()}")
            return 2
        try:
            with urllib.request.urlopen(f"{local_url()}/v1/audio/voices", timeout=10) as r:
                payload = json.loads(r.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"  FAIL: {e}")
            return 1
        raw = payload.get("voices", payload)
        names = sorted(
            v if isinstance(v, str) else (v.get("name") or v.get("id") or str(v))
            for v in raw
        )
        print(f"{len(names)} voices at {local_url()}:")
        for n in names:
            print(f"  {n}")
        return 0

    if not args.test:
        p.print_help()
        return 0

    backend = active_backend()
    src = key_source()
    print(f"Backend: {backend or 'none'}")
    print(f"Source:  {src}")
    if src == "unset":
        print("  → no local server and no API key. See docs/SKILL-tts.md.")
        return 2

    voice = args.voice or (LOCAL_DEFAULT_VOICE if backend == "local"
                           else GEMINI_DEFAULT_VOICE)
    print(f"Model:   {'local server' if backend == 'local' else GEMINI_TTS_MODEL}")
    print(f"Voice:   {voice}")
    print(f"Text:    {args.text!r}")
    print("Synthesizing…")
    try:
        pcm = synthesize_strict(args.text, voice)
    except TtsError as e:
        print(f"  FAIL: {e}")
        return 1

    print(f"  OK — received {len(pcm)} bytes of L16 PCM (24 kHz mono).")

    if args.speak:
        import struct
        import subprocess
        import tempfile

        sample_rate = 24000
        n_samples = len(pcm) // 2
        byte_rate = sample_rate * 2
        wav_header = (
            b"RIFF"
            + struct.pack("<I", 36 + len(pcm))
            + b"WAVE"
            + b"fmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, byte_rate, 2, 16)
            + b"data"
            + struct.pack("<I", len(pcm))
        )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_header + pcm)
            wav_path = f.name
        print(f"  Playing via afplay: {wav_path}")
        try:
            subprocess.run(["afplay", wav_path], check=False)
        except FileNotFoundError:
            print("  (afplay not found — non-macOS? PCM file kept at the path above.)")
            return 0
        os.unlink(wav_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
