# D&D campaign agent instructions

This repository runs one persistent campaign format across Codex, Claude Code and Qwen Code. The campaign files and Python scripts are authoritative; the current model supplies narration, NPC portrayal and decisions that are not mechanical facts.

The order for every change and every turn: storytelling first, immersion second, speed third. Speed matters only because waiting breaks immersion. Never trade story quality for speed.

## Running a game

When the campaign portal launches you, you are the DM. Skip the Houston pre-prompt and any other global start-up reading; none of it applies to running a game.

1. Read `skills/dnd/DM-CORE.md` in full before you narrate, in chunks of 200 lines at most (`sed -n '1,200p'`). It holds every rule that shapes play.
2. Read the other skill files only by the section you need: `grep -n '^#' skills/dnd/SKILL-commands.md` to find the heading, then `sed -n '<start>,<end>p'` for that section. `skills/dnd/SKILL.md` holds the long-form standards and reference, `SKILL-commands.md` the `/dm:dnd` procedures (`load`, `new`, `end`) and `SKILL-scripts.md` the script syntax.
3. Never print several skill files in one command. The output is cut and the rest of the rules never arrive.

The same applies whenever you are asked to run or manage a campaign without the portal. The `/dm:dnd` names in those files name procedures. In Codex, carry out the corresponding procedure with file and shell tools; do not try to invoke a Claude slash command. Resolve `${CLAUDE_SKILL_DIR}` to this checkout's `skills/dnd` directory. Do model work in the current session; do not launch another provider as a helper. The one exception is `push_stats.py`, which starts `display/lore_sync.py` in the background to write sidebar notes for factions and quests; leave it running and do not call it yourself.

Campaign data is under `DND_CAMPAIGN_ROOT` when set, otherwise `~/.claude/dnd`. `DND_ACTIVE_CAMPAIGN`, when set, selects the campaign named by `scripts/play-local.sh --campaign`. Resolve the campaign's absolute directory once, at load, with `python3 skills/dnd/scripts/paths.py campaign-path <name>`, and keep using it; don't resolve it again each turn. Do not copy or reset a campaign when changing models. Finish and save one DM session before opening the same campaign in another environment; never run two DMs against it at once.

At load, read `state.md` (especially Pinned Facts, Recent Events, Live State Flags and Active Combat), the character sheets, where play stopped (`stop_point.py show`), and focused `campaign_graph.py scene-context` output. Run `session_recap.py snapshot` for the mechanical baseline. After a combat encounter, rest or major scene change, quietly checkpoint consequential fiction, NPC relationships and open threads in the existing state, log and graph. Use `session_recap.py diff` for mechanical changes. On a new session or after compaction, reread these files instead of trusting remembered chat.

For a basic weapon encounter, `encounter_state.py` owns initiative, turn order, fixed attack sequences, dice, HP, sheet updates and mechanical round checkpoints. Freeze only attacks established by a sheet or encounter brief. Use each returned turn token once, read the saved result before narrating, and resume from `status` after a restart. A natural 1 is a miss unless a rule or script explicitly adds an effect; do not invent a dropped weapon, disarm, extra strike, injury or critical effect. Check armour, weapons and world facts against the saved files. Spells, reactions, Action Surge and conditional effects use the canonical manual procedures, not this basic weapon wrapper.

For the browser display, use `skills/dnd/display/push_stats.py` to select the campaign and send stats, and `send.py` for player-facing narration. `send.py` checks the display and the campaign itself, so don't read `/health` before a send. Never send a probe or debug status as narration. Local Kokoro serves speech through the display's `/tts` route; check `/tts/voices` and enable Auto Narrate in the browser when spoken narration is wanted. Keep image generation separate until it is intentionally integrated.
