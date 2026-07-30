# Narrator TTS setup (optional)

The display companion can read narrator and NPC blocks aloud. It's optional, off by default, and the rest of the skill works fine without it.

There are two backends. Pick either — the display can't tell them apart.

| | **Local** | **Gemini Flash TTS** |
|---|---|---|
| Cost | Free | ~3¢ per 3-hour session |
| API key | None | Google AI Studio key |
| Network | Runs offline | Cloud |
| Voices | Whatever your server ships (Kokoro: ~67) | 9 curated of 30 |
| Setup | Start a container, ~5 min | Mint a key, ~5 min |
| Languages | Depends on the model | 24, auto-detected |

If you skip this guide entirely, the display still renders text exactly as it does today — no audio, no warnings, no behavior change.

## Which backend runs

Controlled by `DND_TTS_BACKEND`:

| Value | Behavior |
|---|---|
| `auto` *(default)* | Use the local server if it answers; otherwise Gemini if a key is configured; otherwise stay silent. |
| `local` | Always local. A stopped server is reported as an error rather than quietly failing over. |
| `gemini` | Always Gemini, same. |

`auto` means a running local server simply wins, and stopping it falls back to Gemini without touching config.

Check what's live at any time:

```bash
python3 skills/dnd/display/tts.py --test
```

```
Backend: local
Source:  local:http://127.0.0.1:8880
Model:   local server
Voice:   bm_fable
Text:    'Hello, narrator voice test. The torchlit hall awaits.'
Synthesizing…
  OK — received 191592 bytes of L16 PCM (24 kHz mono).
```

## Setup A — local server (free, no API key)

Any server exposing OpenAI-compatible `/v1/audio/speech` works. [Kokoro](https://github.com/remsky/Kokoro-FastAPI) is the easiest and sounds good:

```bash
# CPU — fine on Apple Silicon (~0.27 real-time factor)
docker run -d --name kokoro-tts --restart unless-stopped \
  -p 127.0.0.1:8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest
```

That's the whole setup — `auto` finds it on `http://127.0.0.1:8880` with no further config. Verify:

```bash
python3 skills/dnd/display/tts.py --test --speak
python3 skills/dnd/display/tts.py --list-voices    # all voices the server offers
```

To point at a different host or port — a server on another machine, say — set either:

```bash
export DND_TTS_LOCAL_URL=http://192.168.1.50:8880
# …or persist it:
mkdir -p ~/.config/claude-dnd && chmod 700 ~/.config/claude-dnd
echo "http://192.168.1.50:8880" > ~/.config/claude-dnd/tts.url
```

`DND_TTS_LOCAL_MODEL` overrides the model name sent to the server (default `kokoro`) if yours expects something else.

Voice names are passed through untouched, so anything your server accepts works — including Kokoro's blends, e.g. `bm_lewis+am_fenrir` for something that isn't quite one person.

## Setup B — Gemini Flash TTS

Below. Uses a free Google account and gets you running in ~5 minutes.

## What you get

- A speaker button at the bottom of every narrator and NPC block. Click to hear that block read aloud.
- A 9-voice dropdown (4 male, 5 female) sitting next to the speaker button. Change voice mid-session.
- An optional **Auto Narrate** toggle in the top-right audio controls. When on, every new narrator/NPC block auto-plays in **your browser only** — perfect for a TV or main-display cast device while player phones stay quiet.
- Multi-language support — Gemini auto-detects the language from the text, so a campaign played in Spanish, Japanese, Hindi, or any of the [24 supported languages](https://ai.google.dev/gemini-api/docs/speech-generation) just works.

## What it costs

Gemini Flash TTS bills per character. Typical narration block is ~600 characters; current pricing is ~$0.001 per block. A 3-hour session with ~30 narration blocks costs roughly 3¢. **The free tier handles casual use** — you only need to enable billing if you hit rate-limit errors or if you're a new AI Studio account (Google requires prepaid billing for new accounts as of 2026).

## Setup — three steps, ~5 minutes

### 1. Get a Gemini API key from Google AI Studio

This is the easiest path. No `gcloud`, no Cloud Console, no service accounts.

1. Visit **https://aistudio.google.com/apikey** and sign in with your Google / Gmail account. Accept the terms on first visit.
2. Click **Create API key**. If it asks which project to use, accept the default — Google will create one.
3. Copy the key. It looks like `AIza...` and is roughly 39 characters.

### 2. Save the key to your local config

```bash
mkdir -p ~/.config/claude-dnd && chmod 700 ~/.config/claude-dnd

# Paste the key when prompted, press Return, then Ctrl-D:
cat > ~/.config/claude-dnd/tts.key

chmod 600 ~/.config/claude-dnd/tts.key
```

The skill reads from this path automatically. If you'd rather use an environment variable, export `DND_TTS_KEY` (or `GEMINI_API_KEY`) instead and skip the key file — env vars take precedence.

### 3. Verify

From the skill base directory:

```bash
python3 display/tts.py --test
```

You should see:

```
API key source: file:/Users/you/.config/claude-dnd/tts.key
Model: gemini-2.5-flash-preview-tts
Voice: Enceladus
Text:  'Hello, narrator voice test. The torchlit hall awaits.'
Calling Gemini Flash TTS…
  OK — received 76800 bytes of L16 PCM (24 kHz mono).
```

To also hear it (macOS only):

```bash
python3 display/tts.py --test --speak
```

If verification fails, check the **Troubleshooting** table at the bottom.

## Using it during a session

Once the key is configured and the display companion is running:

- A small speaker icon appears at the bottom-right of every narrator (`.dm-block`) and NPC (`.npc-block`) block. Click to play. Click again to stop.
- A **Voices** dropdown next to it lets you switch narrator voice. The selection persists per-campaign in `state.md → ## Session Flags → tts_voice: <name>`.
- The **Auto Narrate** row in the top-right audio controls is per-browser — toggle it on for your TV cast, off on your player phones. Setting is saved in `localStorage`.

Player input blocks, dice-roll blocks, and tutor/help blocks intentionally **don't** get a speaker button — they're metadata, not narrative voice. The 2000-character cap on the synthesis endpoint is the upper bound; longer narration blocks are truncated server-side.

## Voice catalog

The dropdown asks the server which backend is live (`GET /tts/voices`) and lists that backend's voices, so the two catalogs never mix. A voice remembered from the other backend is replaced with the current backend's default rather than being sent to a server that would reject it.

### Local (Kokoro)

Curated to match the Gemini catalog's size so the dropdown stays scannable. The server accepts any of its ~67 voices — `--list-voices` prints them all, and anything you name in the ramp below works.

| Group | Voice | Notes |
|---|---|---|
| Male | **bm_fable** *(default)* | Deep, measured — classic narrator |
| Male | bm_george | Warm, older — mentors, innkeepers |
| Male | bm_lewis | Dry, clipped — officials, sceptics |
| Male | am_fenrir | Rough, growling — feral characters |
| Male | am_onyx | Low, heavy — heavies and villains |
| Female | af_heart | Clear, grounded — narrator alternate |
| Female | af_bella | Bright, expressive — heroic |
| Female | bf_alice | Crisp, formal — nobles, scholars |
| Female | bf_emma | Mature, warm — elders |
| Female | af_sky | Light, airy — fey, sprites |

Blends work too: `"bm_lewis+am_fenrir"` gives a voice that isn't quite one person — useful for something wearing a body it didn't grow.

### Gemini

Curated 9-voice subset from Gemini's 30-voice catalog, scoped for narrative DM voices.

| Group | Voice | Notes |
|---|---|---|
| Male | Charon | Low, gravelly — heavies, villains |
| Male | **Enceladus** *(default)* | Deep, measured — classic narrator |
| Male | Fenrir | Rough, growling — feral characters |
| Male | Umbriel | Soft, reflective — sages and elders |
| Female | Aoede | Clear, bright — heroic / informative |
| Female | Gacrux | Mature, warm — innkeepers, mentors |
| Female | Kore | Youthful, energetic |
| Female | Vindemiatrix | Crisp, formal — nobles, scholars |
| Female | Zephyr | Light, airy — fey, sprites |

To expand the dropdown to Gemini's full 30 voices, edit `_TTS_VOICES_MALE` / `_TTS_VOICES_FEMALE` in `display/templates/index.html` and add the new names to `VALID_VOICES` in `display/tts.py`. The full catalog is documented at [Google's speech-generation guide](https://ai.google.dev/gemini-api/docs/speech-generation).

## Per-browser cost surfacing

Each player clicking the speaker button on the same narration block produces a **separate** call to Gemini — there's no server-side caching by content hash. A 4-player table where everyone clicks every block roughly 4× the per-block cost. If that becomes a concern, two practical mitigations:

1. Use **Auto Narrate on the casting TV only** — players hear the audio from the TV speaker and don't click their own phones.
2. Set a daily spend cap on your Google billing project at [console.cloud.google.com/billing](https://console.cloud.google.com/billing).

## Multi-language sessions

Gemini Flash TTS auto-detects the input language from the text content. To play a Spanish-language campaign, just narrate in Spanish — the same `/tts` endpoint comes back synthesized correctly. The voice catalog stays identical across languages.

To also wire up SFX trigger packs (sword-clash sounds, magic shimmer, etc.) for non-English narration, set the active SFX languages either via environment:

```bash
export DND_SFX_LANGUAGES=en,es     # English first, then Spanish
```

…or per-campaign via `state.md → ## Session Flags`:

```
sfx_languages: en,zh
```

The skill currently ships SFX packs for all 24 Gemini-supported languages (`ar`, `bn`, `de`, `en`, `es`, `fr`, `hi`, `id`, `it`, `ja`, `ko`, `mr`, `nl`, `pl`, `pt`, `ro`, `ru`, `ta`, `te`, `th`, `tr`, `uk`, `vi`, `zh`). Community PRs to extend any pack are welcome.

## Path B — `gcloud` restricted key (advanced, optional)

If you already use the `gcloud` CLI and would rather mint a key scoped to *only* the TTS API — so a leak can't reach Cloud Storage, BigQuery, or other Google services on the same project — use this path:

```bash
PROJ=my-dnd-tts                  # any globally-unique project id
BILLING=YOUR-BILLING-ID          # gcloud billing accounts list

gcloud projects create "$PROJ"
gcloud billing projects link "$PROJ" --billing-account="$BILLING"
gcloud services enable generativelanguage.googleapis.com --project="$PROJ"

mkdir -p ~/.config/claude-dnd && chmod 700 ~/.config/claude-dnd
gcloud alpha services api-keys create \
  --project="$PROJ" \
  --display-name="claude-dnd-tts" \
  --api-target=service=generativelanguage.googleapis.com \
  --format='value(response.keyString)' \
  > ~/.config/claude-dnd/tts.key
chmod 600 ~/.config/claude-dnd/tts.key
```

The `--api-target` restriction means a leaked key can only call `generativelanguage.googleapis.com` on this specific project. Disable / rotate without affecting any other surface.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `--test` says `Backend: none` / `Source: unset` | No local server reachable **and** no Gemini key. Start the container, or save a key to `~/.config/claude-dnd/tts.key`. |
| `--test` picks `gemini` when you wanted local | The local server isn't answering — check `docker ps` and `curl $DND_TTS_LOCAL_URL/v1/audio/voices`. Set `DND_TTS_BACKEND=local` to make the failure explicit instead of silent. |
| Local backend: "network: [Errno 61] Connection refused" | Server not running, or bound to a different port than `DND_TTS_LOCAL_URL`. |
| Local backend: "http 400" mentioning voice | The server doesn't know that voice name — check `--list-voices`. |
| Dropdown shows Gemini names against a local server | The display is cached; hard-reload the page. `/tts/voices` is fetched once at load. |
| `python3 display/tts.py --test` says "API key: unset" | No env var **and** no key file — save your key to `~/.config/claude-dnd/tts.key`. |
| Speaker button shows "TTS 401" | API key invalid or disabled — re-mint at https://aistudio.google.com/apikey. |
| Speaker button shows "TTS 403" | Key not authorized for `generativelanguage.googleapis.com` (Path B keys), or billing not configured on a new AI Studio account. |
| Speaker button shows "TTS 429" | Free-tier rate limit, or new AI Studio account without billing — enable billing at https://aistudio.google.com or set a prepaid balance. |
| Speaker button shows "TTS 503" | Server reports TTS not configured — re-verify the key file and restart the display. |
| Audio doesn't play but no error label | Check device volume; on iOS Safari, click the speaker once to grant the AudioContext gesture, then auto-narrate will work for the rest of the session. |
| 1-3 second delay before audio starts | Normal — Gemini Flash TTS synthesis latency. Type Speed `Fast` paired with auto-narrate gives the tightest pairing of text and voice. |

## How to disable

TTS goes quiet when neither backend is available, so remove whichever you configured:

```bash
rm ~/.config/claude-dnd/tts.key        # Gemini
docker stop kokoro-tts                 # local
```

The speaker buttons disappear from the display on next page load. Nothing else changes.

To keep both configured but silence audio for one session, set `DND_TTS_BACKEND` to a backend you haven't set up — it reports as unconfigured rather than failing over.
