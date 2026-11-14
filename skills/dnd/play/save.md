# Saving and wrapping up

Read when the portal asks you to save, or at Save & close.

- Close on a hook: a consequence landing, or a decision still open.
- Whenever the portal asks you to save, record where play stopped. Pipe your last six to eight exchanges, oldest first and word for word, into `stop_point.py write --campaign X --where "<one line>" --waiting "<open question or pending roll>"` as JSON: `[{"who": "Players", "text": "..."}, {"who": "DM", "text": "..."}]`. Include the players' last inputs and your final narration.
- The portal's Save & close button asks for the wrap-up, then closes you. Run the `end` procedure in `SKILL-commands.md` with no questions: it saves, writes the recap, records faction moves, checks the arc and refreshes Next Hooks and Clocks.
- Write this session's entry in `session-log.md` with its `### Recap`, built from this session's lines in `turn-log.md` (the scribe's log; never read the whole file, only from its newest `## Play from` heading).
- Refresh `## Story So Far` (one paragraph, at most 150 words) and update `state.md`: in-world date, Active Quests, Clocks, Next Hooks. Save every character sheet and the party roster.
- `## Continuity Archive` keeps the last five sessions; fold older ones into Story So Far and move their bullets to the end of `session-log-archive.md`.
- Award any milestone XP still owed: `xp.py award --campaign X --characters "A,B" --difficulty medium --type noncombat --note "..."`.
