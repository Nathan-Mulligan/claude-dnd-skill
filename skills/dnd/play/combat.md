# Combat

Read once when a fight starts. Commands are shortened as in DM-CORE.md; `X` is the campaign.

## Starting
- Put `--music-event combat-start` on the first combat narration, and `--music-event combat-end` on the first narration after the fight.
- A basic weapon fight (fixed attacks from the sheets or the encounter brief) runs through `encounter_state.py`. It owns initiative, turn order, attack rolls, damage, HP, the sheets' HP lines and `## Active Combat`:
  - `encounter_state.py start --campaign X --combatants '[{"name":"Nella Reed","type":"pc","dex_mod":0,"hp":12,"ac":14,"attack_bonus":4,"damage":"1d8+2"},{"name":"Bandit","type":"npc","dex_mod":1,"hp":11,"ac":12,"attack_bonus":3,"damage":"1d6+1"}]'`
  - Two or more fixed strikes: `"attacks":[{"name":"Claw","attack_bonus":4,"damage":"1d6+2"},{"name":"Bite","attack_bonus":3,"damage":"1d4+1"}]` in place of `attack_bonus` and `damage`. Only strikes the sheet or brief grants.
  - Each turn: `encounter_state.py attack --campaign X --token '<turn_token>' --target '<name>'`, or `pass --campaign X --token '<turn_token>'`. Use each token once and narrate from the saved result.
  - After a restart, `encounter_state.py status --campaign X` before anything else. When one side is down: `push_stats.py --turn-clear`, then `encounter_state.py archive --campaign X`.
- Anything else (spells, reactions, Sneak Attack, Action Surge, conditional damage) is manual: `combat.py init '<JSON>'` for initiative, `push_stats.py --turn-order '{"order":[...],"current":"NAME","round":1}'`, the state JSON under `## Active Combat`, and `dice.py` for each roll.
- Monster stats: `lookup.py monster "<name>"`. Never invent a stat block when the lookup has one.

## Each round
- A player rolls their own attacks in `roll_mode: players`: one `send.py --dice-request ... --wait` call with the line naming the target's AC as its heredoc (DM-CORE section 4).
- NPC and monster attacks: roll with `dice.py` (or `encounter_state.py attack`) before the narration call, then put the roll lines in its `@dice` block.
- The narration call carries every HP and condition change as flags: `--stat-hp "NAME:CUR:MAX"`, `--stat-condition-add "NAME:Poisoned"`, `--stat-slot-use "NAME:1"`, `--effect-start "NAME:Bless:10r:conc"`.
- Turn pointer and conditions go in the bookkeeping call: `push_stats.py --turn-current NAME`, `tracker.py -c X effect tick NAME`.
- A natural 1 is a miss and nothing more, unless a rule or script says otherwise. No dropped weapons or invented injuries.
- Show the enemy's intent before it acts when the party could still answer it. A fight that has stopped being in doubt ends: the enemy flees, surrenders or bargains.

## After the fight
- Narrate the aftermath in one call with `--music-event combat-end`.
- Award combat XP in the bookkeeping call: `xp.py award --campaign X --characters "A,B" --difficulty easy|medium|hard|deadly --type combat [--monsters "bandit:1/8:2"] --note "..."`, then `send.py --xp-award '<json>'`.
- Update the sheets' HP if `encounter_state.py` didn't, then `push_stats.py --party X`. Clear `## Active Combat` and run `tracker.py -c X clear`.
