// Measure how closely narration text follows the Kokoro voice in a real browser.
//
//   node scripts/measure_tts_sync.mjs --url http://localhost:5089 \
//     --send "python3 /tmp/x/send.py" --mode sync --pw /path/to/playwright-core
//
// Runs one scene against a running display (opener + rest, a dice block in the
// middle, an NPC line in its own voice, one more narration line) and prints
// JSON: per TTS chunk, how far the text reveal started after that chunk's audio
// started, and how far it finished after the audio ended (negative = text
// ahead of the voice), plus time to first audio.
//
// --mode sync       Auto Narrate on, real /tts
// --mode off        Auto Narrate off
// --mode fail       Auto Narrate on, every /tts request answers 502
// --mode suspended  Auto Narrate on, audio blocked until a click (never given)
// --scene FILE replaces the scene, as a JSON list of [ms after start, [flags], text].
//
// Use a throwaway display: the scene is written into its campaign log.
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';

const arg = (name, dflt) => {
  const i = process.argv.indexOf('--' + name);
  return i > 0 ? process.argv[i + 1] : dflt;
};
const URL_ = arg('url', 'http://localhost:5089');
const SEND = arg('send', 'python3 send.py').split(' ');
const MODE = arg('mode', 'sync');
const PW = arg('pw', 'playwright-core');
const CHROME = arg('chrome', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');

const { chromium } = createRequire(import.meta.url)(PW);

// --scene FILE: a JSON list of [ms after start, [send.py flags], text] to use instead.
const SCENE_FILE = arg('scene', '');
const DEFAULT_SCENE = [
  [0, ['--opener'], 'The rain eases as you reach the ford. Lanterns bob on the far bank.'],
  [3000, [], 'Three riders wait there, cloaks dark with water, and the eldest raises a gloved hand. ' +
    'Behind them a wagon sits axle-deep in the mud, its team steaming in the cold. ' +
    'Somewhere downstream a heron cries once and is still.'],
  [3600, ['--dice'], 'Perception (Elbane): d20+3 = 17'],
  [4200, ['--npc', 'Captain Oren Vale', '--npc-voice', 'am_michael'],
    'Hold there. Nobody crosses tonight without the warden\'s mark, and I see none on you.'],
  [4800, [], 'The captain\'s eyes flick to the wagon, then back to you.'],
];
const SCENE = SCENE_FILE ? JSON.parse(readFileSync(SCENE_FILE, 'utf-8')) : DEFAULT_SCENE;

function send(flags, text) {
  return new Promise((resolve, reject) => {
    const p = spawn(SEND[0], [...SEND.slice(1), ...flags], { stdio: ['pipe', 'ignore', 'inherit'] });
    p.on('exit', code => (code ? reject(new Error('send.py exited ' + code)) : resolve()));
    p.stdin.end(text);
  });
}

// Runs in the page before its own scripts.
function instrument(mode) {
  if (mode === 'off') localStorage.setItem('dnd_tts_auto', '0');
  else localStorage.setItem('dnd_tts_auto', '1');
  if (mode === 'suspended') {
    // Stand in for Chrome's autoplay block: the context never starts.
    const Real = window.AudioContext;
    window.AudioContext = class extends Real {
      constructor(...a) { super(...a); super.suspend(); }
      resume() { return Promise.resolve(); }
    };
  }
  const log = window.__sync = { tts: [], audio: [], text: [], pending: 0 };
  const origFetch = window.fetch;
  window.fetch = function (url, opts) {
    if (String(url).endsWith('/tts') && opts && opts.body) {
      const body = JSON.parse(opts.body);
      if (!body.local_only) {
        log.tts.push({ t: performance.now(), text: body.text, voice: body.voice });
        log.pending++;
        return origFetch.apply(this, arguments).finally(() => { log.pending--; });
      }
    }
    return origFetch.apply(this, arguments);
  };
  const start = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function (when) {
    const ctx = this.context;
    // Dice and other sound effects play on their own context; only narration counts.
    // eslint-disable-next-line no-undef
    if (typeof _ttsCtx === 'undefined' || ctx !== _ttsCtx) return start.apply(this, arguments);
    const at = performance.now() + Math.max(0, (when || 0) - ctx.currentTime) * 1000;
    log.audio.push({ at, clock: Math.max(when || 0, ctx.currentTime) * 1000,
      dur: this.buffer.duration * 1000, state: ctx.state });
    return start.apply(this, arguments);
  };
  const count = () => {
    let n = 0;
    document.querySelectorAll('#text-content .dm-block > p, #text-content .npc-block > p')
      .forEach(p => { n += p.textContent.replace(/\s/g, '').length; });
    return n;
  };
  addEventListener('DOMContentLoaded', () => {
    let last = -1;
    new MutationObserver(() => {
      const n = count();
      // The audio clock is what the listener hears; under heavy CPU load it can
      // run behind performance.now(), so lags are measured on it when it exists.
      // eslint-disable-next-line no-undef
      const ctx = typeof _ttsCtx !== 'undefined' && _ttsCtx ? _ttsCtx : null;
      if (n !== last) { last = n; log.text.push({ t: performance.now(), clock: ctx ? ctx.currentTime * 1000 : null, n }); }
    }).observe(document.getElementById('text-content'), { childList: true, subtree: true, characterData: true });
  });
}

function analyse(log, sentAt) {
  // Text already on the page (a replayed log) is not part of this scene.
  const before = log.text.filter(e => e.t <= sentAt);
  const base = before.length ? before[before.length - 1].n : 0;
  log.text = log.text.filter(e => e.t > sentAt).map(e => ({ ...e, n: e.n - base }));
  const firstAt = (n) => (log.text.find(e => e.n >= n) || {}).t;
  const firstClock = (n) => (log.text.find(e => e.n >= n) || {}).clock;
  const out = { chunks: [], textEnd: log.text.length ? Math.round(log.text[log.text.length - 1].t - sentAt) : null };
  let offset = 0;
  const spoken = log.tts.filter(r => r.text);
  spoken.forEach((req, i) => {
    const n = req.text.replace(/\s/g, '').length;
    const a = log.audio[i];
    const row = { chunk: req.text.slice(0, 40), voice: req.voice, chars: n };
    if (a) {
      const s = firstClock(offset + 1), e = firstClock(offset + n);
      if (s != null && e != null) {
        row.startLag = Math.round(s - a.clock);
        row.endLag = Math.round(e - (a.clock + a.dur));
      } else {
        row.startLag = Math.round(firstAt(offset + 1) - a.at);
        row.endLag = Math.round(firstAt(offset + n) - (a.at + a.dur));
      }
      row.wallStartLag = Math.round(firstAt(offset + 1) - a.at);
    }
    out.chunks.push(row);
    offset += n;
  });
  if (log.tts.length && log.audio.length) {
    out.firstAudioAfterRequest = Math.round(log.audio[0].at - log.tts[0].t);
    out.firstAudioAfterSend = Math.round(log.audio[0].at - sentAt);
  }
  out.firstTextAfterSend = log.text.length ? Math.round(log.text.find(e => e.n > 0).t - sentAt) : null;
  const lags = out.chunks.filter(c => c.startLag !== undefined);
  if (lags.length) {
    const worst = k => Math.max(...lags.map(c => Math.abs(c[k])));
    out.worstStartLag = worst('startLag');
    out.worstEndLag = worst('endLag');
  }
  const clocked = log.text.filter(e => e.clock != null);
  if (clocked.length > 1) {
    const [a, b] = [clocked[0], clocked[clocked.length - 1]];
    out.audioClockRate = +((b.clock - a.clock) / (b.t - a.t)).toFixed(3);
  }
  out.totalChars = log.text.length ? log.text[log.text.length - 1].n : 0;
  out.audioStates = [...new Set(log.audio.map(a => a.state))];
  return out;
}

const flags = ['--mute-audio'];
if (MODE !== 'suspended') flags.push('--autoplay-policy=no-user-gesture-required');
const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: flags });
try {
  const page = await browser.newPage();
  // Voice previews preload on page load and would compete with the scene for Kokoro.
  await page.route('**/tts', route => {
    const body = JSON.parse(route.request().postData() || '{}');
    if (body.local_only) return route.fulfill({ status: 503, body: 'off for measurement' });
    if (MODE === 'fail') return route.fulfill({ status: 502, body: 'forced failure' });
    return route.continue();
  });
  await page.addInitScript(instrument, MODE);
  await page.goto(URL_, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2500);
  const sentAt = await page.evaluate(() => performance.now());
  const t0 = Date.now();
  for (const [at, f, text] of SCENE) {
    await new Promise(r => setTimeout(r, Math.max(0, at - (Date.now() - t0))));
    await send(f, text);
  }
  // Wait until the text has stopped changing and every scheduled clip has ended.
  for (let i = 0; i < 180; i++) {
    await page.waitForTimeout(1000);
    const done = await page.evaluate(() => {
      const l = window.__sync, now = performance.now();
      const lastText = l.text.length ? l.text[l.text.length - 1].t : 0;
      const audioEnd = Math.max(0, ...l.audio.map(a => a.at + a.dur));
      return !l.pending && now - lastText > 4000 && now > audioEnd + 1000;
    });
    if (done && Date.now() - t0 > 12000) break;
  }
  const log = await page.evaluate(() => window.__sync);
  const raw = process.argv.includes('--raw') ? { raw: { ...log, sentAt } } : {};
  console.log(JSON.stringify({ mode: MODE, ...analyse(log, sentAt), ...raw }, null, 1));
} finally {
  await browser.close();
}
