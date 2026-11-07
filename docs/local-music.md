# Local scene music on the M5

The display can call an already installed Stable Audio 3 Medium MLX runtime on the same Mac. It does not download weights, call a paid service, or run a model on a browser device. Narration, Kokoro and existing sound effects continue if music is unavailable.

## Configure the existing M5 install

Run this once on the M5, with paths to the installed `sa3` wrapper and Hugging Face cache:

```bash
python3 scripts/configure-local-music.py \
  --cli /Users/agencyastro/Desktop/Agents/outputs/dnd-stable-audio3-trial-20261107/source/optimized/mlx/sa3 \
  --hf-home /Users/agencyastro/Desktop/Agents/outputs/dnd-stable-audio3-trial-20261107/hf-cache
```

The command writes only `music-config.json` in the display runtime directory, resolved from `DND_RUNTIME_DIR` or `DND_CAMPAIGN_ROOT` (default `~/.claude/dnd/.runtime`). `DND_MUSIC_CLI` and `DND_MUSIC_HF_HOME` environment variables override that file. Start or restart the display after configuration; do not restart an active display during a campaign. The installed model and weights stay outside the repository. The current trial paths are local M5 paths, not portable project defaults.

On the main display, turn on **Music** in settings and adjust its volume. Player phone views do not play music. The toggle is off until clicked because browsers require a user gesture to unlock audio.

## Scene and combat cues

The shared DM skill tells Qwen, Codex/Terra and Claude to attach a structured music cue to the first narration of a real scene transition. A cue for a cave entry can be sent with:

```bash
python3 skills/dnd/display/send.py --opener \
  --music-event scene --music-setting cave --music-mood ominous << 'DNDEND'
The cave mouth widens into a chamber lit by cultist torches.
DNDEND
```

The same sender accepts `--music-event combat-start` and `--music-event combat-end` with narration. Entering a setting makes a new ten-second intro and sparse two-minute texture. Starting combat stops the current sound and makes one fresh ten-second battle intro, then silence. Ending combat makes a new two-minute texture for the remembered setting. The display's `turn_order` status provides a fallback for combat transitions. There are no character-specific themes in this version.

Each cue carries an ID so send retries cannot start duplicate jobs. Music jobs run one at a time, and a late result from an older scene is discarded. A browser reconnect or display restart loads the current rendered entry without generating another variation. Older generated files stay under the display runtime's `music` directory until a later cleanup pass; do not point that runtime at NAS or `/Volumes`.

## Quality and limits

The ten-second intro uses Medium, eight steps and CFG 1.0. The sparse two-minute bed uses CFG 3.0 with a restrained negative prompt. Positive prompts name acoustic fantasy instruments; the bed permits a faint pad. A negative prompt is not a guarantee against unwanted instruments. The M5 trial previously measured approximately 1.5 seconds for an intro and 7.6 to 8.0 seconds for a sparse two-minute bed while Ollama had no loaded model. Combined latency and memory pressure with Qwen and Kokoro must be verified in a disposable session before treating those times as a live-session guarantee. A bed loops with a short overlap at its join; listen for an audible seam before relying on it at the table.
