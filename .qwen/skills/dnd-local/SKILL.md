---
name: dnd-local
description: Run or manage a persistent D&D campaign in this repository with local Qwen, shared campaign files, dice scripts, cinematic display, and Kokoro narration.
---

# Local D&D DM

This is the Qwen Code entry point for the existing D&D campaign. The canonical game rules and command procedures remain in `skills/dnd/SKILL.md`, `skills/dnd/SKILL-commands.md`, and `skills/dnd/SKILL-scripts.md`. Read the core rules, the procedure for the requested command, and the relevant script syntax. Resolve the literal `${CLAUDE_SKILL_DIR}` in those files to this repository's `skills/dnd` directory when running scripts.

Use `DND_CAMPAIGN_ROOT` when set; otherwise campaign data is in `~/.claude/dnd`. Before every campaign read or write, run `python3 skills/dnd/scripts/paths.py campaign-path <name>` and use that absolute path. Do not hand-build a path from the default shown in the Claude procedures. Claude and Qwen read the same campaign files. Do not copy, migrate, or reset a campaign to change models. Read its current state and session tail before narration. Save state and session logs at the same cadence required by the canonical skill.

The canonical files describe Claude Code tools and `/dm:dnd` commands. In this session, use Qwen Code's corresponding file, shell, and question tools. Treat `dnd load`, `dnd new`, `dnd end`, and other `dnd` requests as the matching `/dm:dnd` procedure. The model routing table is replaced here: do all model work with the current local Qwen model, use the Python scripts for mechanical calculations, and do not launch `claude -p` helper calls. Optional helper features that require a separate Claude call may be skipped, but say so if the player asks for one.

For the display, follow the canonical procedures using `skills/dnd/display/start-display.sh`, `send.py`, `push_stats.py`, and the queue scripts. On load, run `push_stats.py --set-campaign <name>` so the display writes the session tail to that campaign. Send DM narration with plain `send.py` input; reserve `--player NAME` for the player's own words or actions. The existing local TTS configuration, including Kokoro, belongs to the display. If spoken narration is requested, check that `/tts/voices` reports `backend: local` before playing, and tell the player how to enable Auto Narrate in the browser. Never run the display in LAN mode without the player's request. Leave image generation disabled for this variant.

For any d20 roll, preserve the raw die and modifier as separate values. A natural 20 means the raw d20 is 20; a modified total of 20 or more is not a natural 20. The dice script prints `NATURAL 20` only for a raw 20: do not claim a natural 20 or award its Inspiration unless that flag appears in the script output. A natural 20 on an ability check is not a critical hit. Before saving, compare every stated roll and awarded effect against the script output.

If a command procedure is ambiguous because it depends on a Claude-only hook or slash command, preserve the campaign state and explain the gap instead of guessing. Do not rewrite the canonical skill or change Claude settings.
