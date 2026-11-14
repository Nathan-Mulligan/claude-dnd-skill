"""With Auto Narrate on, the words appear as the voice says them.

WHY
===
The typewriter and the Kokoro voice ran on two clocks. Text started typing
the moment it arrived, at 36 ms a character, while the first audio chunk was
still being synthesised, so the page was seconds ahead of the voice by the end
of a paragraph. Two people reading and listening together lost their place.

Now each TTS chunk's span of text is revealed against that chunk's own audio
on the AudioContext clock: start with the audio, finish as it ends. What these
tests pin down is the part that is easy to get wrong:

  * the text-to-chunk mapping must cover every character, in order, even when
    markdown and whitespace make the page text and the spoken text differ;
  * the pacing must come from the audio clock alone, not a second timer;
  * text must never wait on audio that is not coming: a failed fetch, a stop,
    a suspended context or a late chunk all fall back to the plain typewriter.

Driven through node against the real template source.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / "skills" / "dnd" / "display" / "templates" / "index.html"
NODE = shutil.which("node")

_SYNC = re.compile(r"(// ── Voice sync .*?)\nfunction typeNextChar\(\)", re.S)
_QUEUE = re.compile(r"(// ── Narration queue .*?)\nfunction _ttsStopAll\(\)", re.S)


def _slice(pattern: re.Pattern) -> str:
    m = pattern.search(HTML.read_text(encoding="utf-8"))
    if not m:
        raise AssertionError("sync code is no longer where this test slices it")
    return m.group(1)


def _run(script: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                     encoding="utf-8") as f:
        f.write(script)
        path = f.name
    try:
        out = subprocess.run([NODE, path], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
    finally:
        pathlib.Path(path).unlink(missing_ok=True)
    if out.returncode != 0:
        raise AssertionError(out.stderr[:2000])
    return json.loads(out.stdout.strip().splitlines()[-1])


def _sync(body: str) -> dict:
    prelude = """
let _ttsGen = 0;
let _ttsCtx = { state: 'running', currentTime: 0 };
const line = (texts, at = null, dur = 0) =>
  ({ gen: 0, off: false, chunks: texts.map(t => ({ text: t, at, dur })) });
const chars = s => [...s];
"""
    return _run(prelude + _slice(_SYNC) + "\n" + body)


@unittest.skipUnless(NODE, "node not available")
class TextToChunkMapping(unittest.TestCase):

    def test_each_chunk_gets_its_own_span_in_order(self):
        r = _sync("""
        const s = line(['The door opens.', 'A cold wind blows in.']);
        const out = _syncMark(chars('The door opens. A cold wind blows in.'), s);
        const spans = []; let cur = null;
        for (const it of out) {
          if (it && typeof it === 'object' && 'sync' in it) { cur = []; spans.push([it.sync, cur]); }
          else cur.push(it);
        }
        console.log(JSON.stringify({
          texts: spans.map(([, items]) => items.join('')),
          chunkOf: spans.map(([seg]) => s.chunks.indexOf(seg.chunk)),
          n: spans.map(([seg]) => seg.n),
          length: out.length,
        }));
        """)
        self.assertEqual(r["texts"], ["The door opens. ", "A cold wind blows in."])
        self.assertEqual(r["chunkOf"], [0, 1])
        self.assertEqual(r["n"], [13, 17])
        self.assertEqual(r["length"], 37 + 2)

    def test_markdown_markup_does_not_shift_the_boundaries(self):
        r = _sync("""
        const s = line(['Stop right there.', 'Who goes?']);
        const items = ['*', {open: 'em'}, ...chars('Stop'), {close: 'em'}, ...chars(' right there. Who goes?')];
        const out = _syncMark(items, s);
        const i = out.findIndex((it, k) => k > 0 && it && typeof it === 'object' && 'sync' in it);
        console.log(JSON.stringify({ second: out.slice(i + 1).join('') }));
        """)
        self.assertEqual(r["second"], "Who goes?")

    def test_unsynced_text_resets_any_earlier_line(self):
        r = _sync("""
        const out = _syncMark(chars('hi'), null);
        console.log(JSON.stringify({ first: out[0], rest: out.slice(1).join('') }));
        """)
        self.assertEqual(r["first"], {"sync": None})
        self.assertEqual(r["rest"], "hi")


@unittest.skipUnless(NODE, "node not available")
class PacingFollowsTheAudioClock(unittest.TestCase):

    def test_text_waits_for_its_chunk_then_spreads_over_the_audio(self):
        r = _sync("""
        const s = line(['abcd']);
        const seg = _syncMark(chars('abcd'), s)[0].sync;
        _syncSeg = seg;
        const waiting = _syncWait('a');
        s.chunks[0].at = 10; s.chunks[0].dur = 2;
        _ttsCtx.currentTime = 9;
        const beforeStart = _syncWait('a');
        _ttsCtx.currentTime = 10.5; seg.shown = 2;
        const third = _syncWait('c');
        const space = _syncWait(' ');
        console.log(JSON.stringify({ waiting, beforeStart, third, space }));
        """)
        self.assertEqual(r["waiting"], 40, "should poll until the chunk is scheduled")
        self.assertAlmostEqual(r["beforeStart"], 1000, delta=1)
        # item 2 of 4 is due at 10 + 2 * 2/4 = 11s; the clock reads 10.5s
        self.assertAlmostEqual(r["third"], 500, delta=1)
        self.assertAlmostEqual(r["space"], 500, delta=1)

    def test_no_line_means_the_plain_typewriter(self):
        r = _sync("""
        _syncSeg = null;
        console.log(JSON.stringify({ wait: _syncWait('a') }));
        """)
        self.assertIsNone(r["wait"])


@unittest.skipUnless(NODE, "node not available")
class NeverStallOnMissingAudio(unittest.TestCase):

    def _case(self, setup: str) -> dict:
        return _sync("""
        const s = line(['abcd'], 1, 2);
        _syncSeg = _syncMark(chars('abcd'), s)[0].sync;
        """ + setup + """
        console.log(JSON.stringify({ wait: _syncWait('a'), off: s.off }));
        """)

    def test_a_failed_fetch_types_normally(self):
        r = self._case("s.off = true;")
        self.assertIsNone(r["wait"])

    def test_the_stop_button_types_normally(self):
        r = self._case("_ttsGen = 1;")
        self.assertIsNone(r["wait"])
        self.assertTrue(r["off"])

    def test_suspended_audio_types_normally(self):
        r = self._case("_ttsCtx.state = 'suspended';")
        self.assertIsNone(r["wait"])
        self.assertTrue(r["off"])

    def test_no_audio_context_types_normally(self):
        r = self._case("_ttsCtx = null;")
        self.assertIsNone(r["wait"])

    def test_late_audio_gives_up_after_the_timeout(self):
        r = self._case("s.chunks[0].at = null; _syncSeg.since = Date.now() - SYNC_WAIT_MAX - 1;")
        self.assertIsNone(r["wait"])
        self.assertTrue(r["off"])

    def test_suspended_audio_is_not_waited_for(self):
        r = self._case("s.chunks[0].at = null; _ttsCtx.state = 'suspended'; _syncSeg.since = Date.now() - 600;")
        self.assertIsNone(r["wait"])

    def test_an_npc_line_whose_audio_failed_shows_at_once(self):
        """An NPC block appeared whole before; without its voice it still does."""
        r = self._case("s.npc = true; s.off = true;")
        self.assertEqual(r["wait"], 0)


@unittest.skipUnless(NODE, "node not available")
class NarrateReportsItsTiming(unittest.TestCase):
    """_narrate's handle is the single source the typewriter reads."""

    def _narrate(self, fetch_ok: bool) -> dict:
        prelude = """
globalThis.window = {};
globalThis.document = { addEventListener() {}, querySelectorAll: () => [] };
const _TTS_HAS_BROWSER = false;
let _ttsAutoNarrate = true, _TTS_AVAILABLE = true, _inputOnlyAudio = false, _ttsVolume = 1;
const _authHeaders = () => ({});
const _ttsChunkText = t => [t];
const _ttsGain = {};
const ctx = {
  state: 'running', currentTime: 5,
  resume: async () => {},
  createBuffer: (c, n, rate) => ({ duration: n / rate, getChannelData: () => new Float32Array(n) }),
  createBufferSource: () => ({ connect() {}, start() {} }),
};
const _getTtsCtx = () => ctx;
globalThis.fetch = async () => FETCH_OK
  ? { ok: true, status: 200, arrayBuffer: async () => new ArrayBuffer(48000) }
  : { ok: false, status: 502 };
""".replace("FETCH_OK", "true" if fetch_ok else "false")
        body = """
const sync = _narrate('The gate creaks open. Beyond it the courtyard lies silent under fresh snow.', 'bm_lewis');
await _ttsPlayChain;
console.log(JSON.stringify({
  n: sync.chunks.length,
  texts: sync.chunks.map(c => c.text),
  at: sync.chunks.map(c => c.at),
  dur: sync.chunks.map(c => c.dur),
  off: sync.off,
}));
"""
        return _run(prelude + _slice(_QUEUE) + "\n" + body)

    def test_each_chunk_carries_its_start_and_length(self):
        r = self._narrate(True)
        self.assertGreaterEqual(r["n"], 2, "the ramp should give a short first chunk")
        self.assertEqual(" ".join(r["texts"]),
                         "The gate creaks open. Beyond it the courtyard lies silent under fresh snow.")
        self.assertAlmostEqual(r["at"][0], 5.03, places=3)
        self.assertEqual(r["dur"], [1.0] * r["n"])
        # back to back on one clock, no gap inside a line
        for a, b in zip(r["at"], r["at"][1:]):
            self.assertAlmostEqual(b - a, 1.0, places=6)
        self.assertFalse(r["off"])

    def test_a_failed_fetch_switches_the_line_to_the_typewriter(self):
        r = self._narrate(False)
        self.assertTrue(r["off"])
        self.assertEqual(r["at"], [None] * r["n"])


class Wiring(unittest.TestCase):
    """Defined-but-never-called is the failure mode this repo has shipped before."""

    @classmethod
    def setUpClass(cls):
        cls.src = HTML.read_text(encoding="utf-8")

    def test_narration_and_npc_lines_pass_their_audio_to_the_text(self):
        self.assertRegex(self.src, r"handleIncomingText\(_txt, _op, _sync\)")
        self.assertRegex(self.src, r"renderNPCBlock\(payload\.npc, payload\.text, false, _sync\)")
        self.assertIn("_syncMark(_mdParse(cleaned), sync)", self.src)

    def test_a_long_wait_rechecks_so_stop_wakes_the_text(self):
        loop = re.search(r"function typeNextChar\(\) \{(.*?)\n\}", self.src, re.S)
        self.assertIn("Math.min(wait, 250)", loop.group(1))

    def test_an_opener_still_waiting_for_audio_keeps_its_joining_space(self):
        fn = re.search(r"function handleIncomingText\(text, opener, sync\) \{(.*?)\n\}", self.src, re.S)
        self.assertRegex(fn.group(1), r"\} else if \(held\) \{")

    def test_the_typewriter_reads_the_audio_clock(self):
        loop = re.search(r"function typeNextChar\(\) \{(.*?)\n\}", self.src, re.S)
        self.assertIsNotNone(loop)
        self.assertIn("_syncWait(item)", loop.group(1))


if __name__ == "__main__":
    unittest.main()
