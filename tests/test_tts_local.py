"""
test_tts_local.py — backend selection + local synthesis path for display/tts.py.

No network: the local reachability probe and both HTTP calls are patched.

Run from repo root:
    python3 -m unittest tests.test_tts_local -v
"""
import importlib
import io
import json
import pathlib
import sys
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd" if (REPO / "skills" / "dnd").is_dir() else REPO
sys.path.insert(0, str(SKILL / "display"))

import tts  # noqa: E402


class _Resp(io.BytesIO):
    """Minimal stand-in for the object urlopen returns in a context manager."""

    def __init__(self, payload: bytes, status: int = 200):
        super().__init__(payload)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _reset(**env):
    """Reload tts with a clean env so the probe cache and config are fresh."""
    keys = ("DND_TTS_BACKEND", "DND_TTS_LOCAL_URL", "DND_TTS_LOCAL_MODEL",
            "DND_TTS_KEY", "GEMINI_API_KEY")
    patcher = mock.patch.dict("os.environ",
                              {k: v for k, v in env.items()}, clear=False)
    patcher.start()
    for k in keys:
        if k not in env:
            import os
            os.environ.pop(k, None)
    importlib.reload(tts)
    return patcher


class BackendSelectionTests(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()
        importlib.reload(tts)

    def test_auto_prefers_local_when_reachable(self):
        _reset()
        with mock.patch.object(tts, "local_available", return_value=True):
            self.assertEqual(tts.active_backend(), "local")
            self.assertTrue(tts.key_source().startswith("local:"))

    def test_auto_falls_back_to_gemini_when_local_down(self):
        _reset(DND_TTS_KEY="AIzaTEST")
        with mock.patch.object(tts, "local_available", return_value=False):
            self.assertEqual(tts.active_backend(), "gemini")
            self.assertEqual(tts.key_source(), "env:DND_TTS_KEY")

    def test_auto_reports_unset_with_neither(self):
        _reset()
        with mock.patch.object(tts, "local_available", return_value=False), \
             mock.patch.object(tts, "_get_api_key", return_value=None):
            self.assertIsNone(tts.active_backend())
            self.assertEqual(tts.key_source(), "unset")

    def test_explicit_backend_does_not_fail_over(self):
        """A misconfigured explicit backend must surface, not silently switch."""
        _reset(DND_TTS_BACKEND="local")
        with mock.patch.object(tts, "local_available", return_value=False):
            self.assertEqual(tts.active_backend(), "local")

        _reset(DND_TTS_BACKEND="gemini")
        with mock.patch.object(tts, "local_available", return_value=True), \
             mock.patch.object(tts, "_get_api_key", return_value=None):
            self.assertEqual(tts.active_backend(), "gemini")
            self.assertEqual(tts.key_source(), "unset")


class LocalUrlTests(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()
        importlib.reload(tts)

    def test_default_url(self):
        _reset()
        self.assertEqual(tts.local_url(), tts.LOCAL_DEFAULT_URL)

    def test_env_wins_and_trailing_slash_stripped(self):
        _reset(DND_TTS_LOCAL_URL="http://box.local:9000/")
        self.assertEqual(tts.local_url(), "http://box.local:9000")


class VoiceCatalogTests(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()
        importlib.reload(tts)

    def test_catalog_and_dynamic_attrs_track_backend(self):
        _reset()
        with mock.patch.object(tts, "local_available", return_value=True):
            cat = tts.voice_catalog()
            self.assertEqual(cat["backend"], "local")
            self.assertEqual(cat["default"], tts.LOCAL_DEFAULT_VOICE)
            self.assertEqual(tts.DEFAULT_VOICE, tts.LOCAL_DEFAULT_VOICE)
            self.assertIn("bm_fable", tts.VALID_VOICES)

        _reset(DND_TTS_KEY="AIzaTEST")
        with mock.patch.object(tts, "local_available", return_value=False):
            cat = tts.voice_catalog()
            self.assertEqual(cat["backend"], "gemini")
            self.assertEqual(tts.DEFAULT_VOICE, tts.GEMINI_DEFAULT_VOICE)
            self.assertIn("Enceladus", tts.VALID_VOICES)

    def test_catalog_groups_are_disjoint_and_complete(self):
        _reset()
        with mock.patch.object(tts, "local_available", return_value=True):
            cat = tts.voice_catalog()
        self.assertFalse(set(cat["male"]) & set(cat["female"]))
        self.assertEqual(set(cat["male"]) | set(cat["female"]),
                         set(tts.LOCAL_VALID_VOICES))
        self.assertIn(cat["default"], cat["male"] + cat["female"])

    def test_unknown_module_attribute_still_raises(self):
        _reset()
        with self.assertRaises(AttributeError):
            tts.NOT_A_REAL_ATTRIBUTE


class LocalSynthesisTests(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()
        importlib.reload(tts)

    def test_request_shape_and_pcm_passthrough(self):
        _reset(DND_TTS_BACKEND="local")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode())
            return _Resp(b"\x01\x02" * 64)

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            pcm = tts.synthesize_strict("The horn sounds.", "bm_lewis")

        self.assertEqual(pcm, b"\x01\x02" * 64)
        self.assertTrue(captured["url"].endswith("/v1/audio/speech"))
        self.assertEqual(captured["body"]["voice"], "bm_lewis")
        self.assertEqual(captured["body"]["input"], "The horn sounds.")
        # PCM keeps the response byte-identical to the Gemini path's L16 output.
        self.assertEqual(captured["body"]["response_format"], "pcm")

    def test_blended_voice_passes_through_unvalidated(self):
        """Kokoro accepts 'a+b' blends; no local allow-list may reject them."""
        _reset(DND_TTS_BACKEND="local")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _Resp(b"\x00" * 32)

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            tts.synthesize_strict("Speak.", "bm_lewis+am_fenrir")
        self.assertEqual(captured["body"]["voice"], "bm_lewis+am_fenrir")

    def test_empty_payload_is_an_error(self):
        _reset(DND_TTS_BACKEND="local")
        with mock.patch("urllib.request.urlopen", lambda *a, **k: _Resp(b"")):
            with self.assertRaises(tts.TtsError):
                tts.synthesize_strict("hi")

    def test_empty_text_rejected_before_any_request(self):
        _reset(DND_TTS_BACKEND="local")
        called = []
        with mock.patch("urllib.request.urlopen",
                        lambda *a, **k: called.append(1) or _Resp(b"x")):
            with self.assertRaises(tts.TtsError):
                tts.synthesize_strict("   ")
        self.assertEqual(called, [])

    def test_text_truncated_to_max_chars(self):
        _reset(DND_TTS_BACKEND="local")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode())
            return _Resp(b"\x00" * 8)

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            tts.synthesize_strict("x" * (tts.MAX_TEXT_CHARS + 500))
        self.assertEqual(len(captured["body"]["input"]), tts.MAX_TEXT_CHARS)

    def test_synthesize_is_silent_on_failure(self):
        _reset(DND_TTS_BACKEND="local")

        def boom(*a, **k):
            raise OSError("connection refused")

        with mock.patch("urllib.request.urlopen", boom):
            self.assertIsNone(tts.synthesize("hi"))

    def test_no_backend_raises_but_synthesize_stays_silent(self):
        _reset()
        with mock.patch.object(tts, "local_available", return_value=False), \
             mock.patch.object(tts, "_get_api_key", return_value=None):
            with self.assertRaises(tts.TtsError):
                tts.synthesize_strict("hi")
            self.assertIsNone(tts.synthesize("hi"))


class ProbeCacheTests(unittest.TestCase):
    def tearDown(self):
        mock.patch.stopall()
        importlib.reload(tts)

    def test_probe_result_is_cached(self):
        _reset()
        calls = []

        def fake_urlopen(url, timeout=None):
            calls.append(url)
            return _Resp(b'{"voices":[]}')

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            self.assertTrue(tts.local_available(force=True))
            tts.local_available()
            tts.local_available()
        self.assertEqual(len(calls), 1, "probe should be cached within its TTL")

    def test_unreachable_server_probes_false(self):
        _reset()

        def boom(*a, **k):
            raise OSError("refused")

        with mock.patch("urllib.request.urlopen", boom):
            self.assertFalse(tts.local_available(force=True))


if __name__ == "__main__":
    unittest.main()
