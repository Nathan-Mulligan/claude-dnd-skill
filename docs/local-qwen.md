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

The repo already has the context system needed for long sessions. `state.md` holds the current situation, pinned facts, recent events and live flags; character sheets hold mechanical state; `session-log.md` and its archive hold history; `session_recap.py` computes character changes; and `graph.json` indexes relationships. The Qwen entry point directs the DM to read the focused sources at load and quietly checkpoint them after rests and major scene changes. There is no second ledger to maintain. For basic weapon encounters, `encounter_state.py` saves exact rolls, turn order and HP in `encounter.json` and writes completed-round checkpoints to both `state.md` and `session-log.md`. The optional every-turn autosave hook is specific to Claude Code; story beats and scene-boundary checkpoints outside the basic weapon path still depend on the model following the skill instructions.

In a fresh-process local test, Qwen correctly recovered a pinned promise, an exact warning, a 10→16 HP change after a mock rest, and the lantern's status from those files and the graph. The relationship extractor needs PyYAML; the display bootstrap now puts it in the campaign-local Python environment, which the launcher uses when present.

## Current verification limit

Dice, two consecutive investigation turns, campaign saves, display routing and Kokoro audio generation passed local checks. Earlier two-character, two-goblin combat trials **failed** when Qwen chose attack dice or turn order itself. With the encounter wrapper, Qwen completed a five-round test encounter across fresh processes on the laptop and M5. The saved archive has all 16 legal actions in initiative order, including a natural 1 without an added penalty; round caps held; both PC sheets ended at the saved HP; and the warning, promise and lit lantern survived the restarts. The wrapper wrote one mechanical checkpoint per round into both campaign files and refreshed Active Combat from the saved encounter. M5's focused suite passed 87 tests. On the M5, Kokoro produced a valid 24 kHz WAV and the display's `/tts` route returned PCM audio; display narration reached the correct campaign. The M5 test used an isolated copy of this branch and a disposable campaign, not a permanent installation.

The five-round trial validated the original one-attack path. Qwen's prose still invented small unestablished details in that trial (plate armour for a character wearing chain and a dropped goblin weapon after a claw attack), so review narration when exact story facts matter. Claude and Codex have not had an equivalent full encounter trial on this branch. Browser speaker playback was not independently confirmed; audio generation was.

The follow-up fixed-sequence path accepts up to four named basic strikes per combatant. An M5 Qwen trial used Mira's rapier attack followed by a goblin's Claw and Bite in one round. The three unique tokens, critical Claw damage, natural-1 Bite miss, PC sheet HP, round cap, checkpoint and display narration matched the saved results. This does not grant attacks automatically from a class or monster name: the sheet or encounter brief must establish the sequence at `start`. Action Surge, spells, reactions and conditional damage remain manual.
