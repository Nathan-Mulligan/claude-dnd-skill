# Local Qwen campaign option

The Claude launcher loads this checkout's plugin for its session; no separate plugin installation is needed. To choose a DM from this checkout:

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

The permanent M5 checkout runs the same main commit as the fork. On 7 November 2026 its full Python suite passed 289 tests, with 3 skips and 111 subtests. Qwen Code loaded a disposable saved campaign, recovered the active scene, both character HP values, a pinned promise and an exact warning, and sent a narration turn and character stats to the M5 display. The existing browser showed the turn with Auto Narrate on; it requested `/tts` twice and both requests returned 200 from local Kokoro. This verifies synthesis and browser requests, not the sound reaching a particular speaker.

The five-round M5 Qwen trial validated the one-attack path: all 16 legal actions were saved in initiative order, including a natural 1 without an added mechanical penalty. Round caps, PC sheets, state checkpoints, and continuity facts survived fresh processes. A later M5 trial validated a fixed Claw-then-Bite sequence with distinct turn tokens. The wrapper accepts up to four named basic strikes per combatant, but never grants them from a class or monster name; the sheet or encounter brief must establish them at `start`. Action Surge, spells, reactions and conditional damage remain manual.

In a paired one-round trial from identical campaign files, Qwen and GPT-6.1 Terra both followed the wrapper's saved initiative, attacks and HP transitions. Terra kept the narration consistent with the saved facts. Qwen invented a dropped rapier after a natural-1 miss, described both PCs as bleeding when only one had lost HP, and its self-audit missed those errors. An HP summary in `state.md` also stayed stale after damage; the shared encounter wrapper now synchronises that compact party line with the saved combat state. Earlier Qwen prose had likewise invented plate armour and a dropped weapon after a claw attack. Treat the saved mechanics as authoritative and review Qwen narration when exact story details matter. This trial does not establish a reliable percentage of overall DM quality. Claude has not had an equivalent full encounter trial on this commit.
