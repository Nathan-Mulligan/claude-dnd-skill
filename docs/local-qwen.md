# Local Qwen campaign option

The existing Claude plugin remains the default. To choose a DM from this checkout:

```bash
scripts/play-local.sh claude
scripts/play-local.sh qwen
# For an existing campaign, select it before play:
scripts/play-local.sh qwen --campaign <name>
```

In the Qwen session, ask to load your campaign by name (for example, `Load my D&D campaign <name>`), or invoke `/skills dnd-local`. The `--campaign` option checks that `state.md` exists, sets the active campaign for display sends, and gives Qwen its absolute path. It works with `claude` too. Both models use the same campaign data root (`~/.claude/dnd` unless `DND_CAMPAIGN_ROOT` is set). Qwen may ask you to approve local script commands during play. End and save the current session before switching models; do not run two DMs against one campaign simultaneously.

The Qwen option uses the locally installed `qwen3.6:35b-mlx` model through Ollama and Qwen Code. For spoken narration, start the existing `kokoro-tts` Docker container and the display, then turn on **Auto Narrate** in the browser. The display's TTS route uses Kokoro locally. Image generation is not connected.

## Campaign continuity

The repo already has the context system needed for long sessions. `state.md` holds the current situation, pinned facts, recent events and live flags; character sheets hold mechanical state; `session-log.md` and its archive hold history; `session_recap.py` computes character changes; and `graph.json` indexes relationships. The Qwen entry point now directs the DM to read the focused sources at load and quietly checkpoint them after encounters, rests and major scene changes. There is no second ledger to maintain. The optional every-turn autosave hook is specific to Claude Code, so Qwen's scene-boundary checkpoints still depend on the model following the skill instructions.

In a fresh-process local test, Qwen correctly recovered a pinned promise, an exact warning, a 10→16 HP change after a mock rest, and the lantern's status from those files and the graph. The relationship extractor needs PyYAML; the display bootstrap now puts it in the campaign-local Python environment, which the launcher uses when present.

## Current verification limit

Dice, two consecutive investigation turns, campaign saves, display routing and Kokoro audio generation passed local checks. A two-character, two-goblin autonomous combat trial **did not pass**: Qwen changed a goblin's damage specification, ran beyond the requested round limit, and invented an extra consequence for a natural 1. In a bounded one-round retry, it issued a second goblin attack in Mira's initiative slot. Do not rely on Qwen to run combat unattended yet. The shared scripts and Claude instructions remain available, but Claude and Codex have not had an equivalent full encounter trial on this branch. Browser speaker playback was not independently confirmed; audio generation was.
