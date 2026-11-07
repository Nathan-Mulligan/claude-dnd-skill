---
name: dnd-local
description: Run or manage a persistent D&D campaign in this repository with local Qwen, shared campaign files, dice scripts, cinematic display, and Kokoro narration.
---

# Local D&D DM

This is the Qwen Code entry point for the existing D&D campaign. The canonical game rules and command procedures remain in `skills/dnd/SKILL.md`, `skills/dnd/SKILL-commands.md`, and `skills/dnd/SKILL-scripts.md`. Read those files before running a campaign. Resolve the literal `${CLAUDE_SKILL_DIR}` in those files to this repository's `skills/dnd` directory when running scripts.

Use `DND_CAMPAIGN_ROOT` when set; otherwise campaign data is in `~/.claude/dnd`. Claude and Qwen read the same campaign files. Do not copy, migrate, or reset a campaign to change models. Read its current state and session tail before narration. Save state and session logs at the same cadence required by the canonical skill.

The canonical files describe Claude Code tools and `/dm:dnd` commands. In this session, use Qwen Code's corresponding file, shell, and question tools. Treat `dnd load`, `dnd new`, `dnd end`, and other `dnd` requests as the matching `/dm:dnd` procedure. The model routing table is replaced here: do all model work with the current local Qwen model, use the Python scripts for mechanical calculations, and do not launch `claude -p` helper calls. Optional helper features that require a separate Claude call may be skipped, but say so if the player asks for one.

For the display, follow the canonical procedures using `skills/dnd/display/start-display.sh`, `send.py`, `push_stats.py`, and the queue scripts. The existing local TTS configuration, including Kokoro, belongs to the display and needs no model-specific changes. Never run the display in LAN mode without the player's request. Leave image generation disabled for this variant.

If a command procedure is ambiguous because it depends on a Claude-only hook or slash command, preserve the campaign state and explain the gap instead of guessing. Do not rewrite the canonical skill or change Claude settings.
