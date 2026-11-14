# New campaign and character setup

Read only when the portal asks for a new campaign or a new character. None of this is play reading.

- Follow `/dm:dnd new` in `SKILL-commands.md` (find it with `grep -n '^## ' SKILL-commands.md`, then read only that section), and `/dm:dnd character new` for each character.
- With a completed questionnaire from the portal, it is the authoritative setup: create every listed character with legal, concept-fitting defaults for blank choices, and ask no routine setup questions.
- Send every setup question you do need to the display with `send.py --setup-question` before waiting. Answers arrive as Party Input until character creation is complete.
- Resolve the campaign folder once with `python3 skills/dnd/scripts/paths.py campaign-path <name>` and create its files from `templates/`.
- Improvised campaigns: write `## Campaign Arc`, two or three `## Next Hooks` and any `## Clocks` before the first scene (travel.md has the formats), and a `Trade policy:` line in `## Pinned Facts` if the party trades.
- Push the party with `push_stats.py --party <name> --replace-players`, clear any setup question, then open the first scene already moving (DM-CORE section 1).
