# D&D Skill — Scripts Reference

Full syntax for all Python helper scripts. Load this file once at `/dm:dnd load`, then it stays in context for the session.

> **Path note:** commands below use `${CLAUDE_SKILL_DIR}` for the skill directory. This file is read verbatim, so that token is **not** auto-expanded here — substitute the absolute skill-dir path (from `SKILL.md`) before running any command, or it will fail with a broken `/scripts/…` path.

---

## Dice Script — `scripts/dice.py`

**MANDATORY.** Every die roll in play — player checks, NPC attacks, saves, damage, ability score gen, anything — must be produced by invoking this script via Bash. **Never sample dice mentally or with inline `random` calls.** The script routes rolls through a local physical-dice server that may surface them on the player's phone for them to cast; rolling in your head bypasses that and breaks the ritual. If the server isn't running the script falls back to local random — so there is no scenario where the script should be skipped.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+5
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py 2d6+3
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py 4d6kh3        # ability score roll
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20 adv       # advantage
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+3 dis     # disadvantage + modifier
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20 --silent  # returns integer only

# Always pass --label so the phone HUD shows what the roll is for:
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+4 --label "Perception check"
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+6 adv --label "Attack — Goblin Boss vs Piper"
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py 2d8+3 --label "Greataxe damage"

# Player rolls — pass --player <pc-name> to route to that player's phone tab:
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+4 --label "Perception" --player piper
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+6 adv --label "Attack" --player piper
# NPC / monster / DM-side rolls — omit --player (routes to the DM channel,
# which auto-rolls server-side if the DM has no tab open).
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+5 --label "Goblin attack"
```

**Routing rule:** if the roll is **for a player character**, pass `--player <pc-name>` (lowercase, matches whatever name the player used in the URL). If the roll is for an NPC/monster/anything the DM resolves, omit `--player` so it doesn't ring the players' phones.

**Etiquette rule (important):** when invoking with `--player`, the player is not staring at their phone — they're listening to you narrate. **Always prompt them out loud before invoking**, so they pick up the phone. Pattern:

> *"Piper — make a Perception check. Cast it."*

Then run the command. The Bash call will block while the player picks up the phone, sees the prompt, and casts; the result returns to you afterward. Without the verbal prompt the player won't know to look, and the call will sit waiting for ~3 minutes before timing out into an auto-roll.

Flags a raw natural 20 or natural 1 without deciding whether the roll was an attack, ability check, or save. Apply critical attack rules only to attacks. If output contains `[auto]` the target's phone wasn't connected and the server rolled itself — no action needed, just narrate the result.

To force-skip the physical roller (e.g. high-volume NPC rolls you don't want to surface): `--auto` flag, or `DND_DICE_PHYSICAL=0 python3 ...`.

---

## Ability Scores Script — `scripts/ability-scores.py`
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/ability-scores.py roll
python3 ${CLAUDE_SKILL_DIR}/scripts/ability-scores.py pointbuy
python3 ${CLAUDE_SKILL_DIR}/scripts/ability-scores.py pointbuy --check STR=15 DEX=10 CON=15 INT=8 WIS=11 CHA=12
python3 ${CLAUDE_SKILL_DIR}/scripts/ability-scores.py modifiers STR=15 DEX=10 CON=15 INT=8 WIS=11 CHA=12
```
Roll mode: generates 3 arrays (4d6kh3 × 6 each). Point buy mode: prints cost table; `--check` validates against the 27-point budget.

---

## XP Script — `scripts/xp.py`
Awards XP for combat and qualifying non-combat encounters. Reads character files from the campaign directory, updates XP, and pushes to the display sidebar. All tables (difficulty thresholds, CR→XP, monster multipliers, level advancement) are codified in the script — the DM only decides the difficulty tier or provides a monster list.

```bash
# Preview — no files modified:
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py calc --level 3 --players 2 --difficulty hard --type combat
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py calc --level 3 --players 2 --monsters "goblin:1/4:3,hobgoblin:1:1"

# Award after a combat encounter — difficulty-rated (use when full monster list is unavailable):
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py award \
  --campaign <name> --characters "Max of Thraxx,Ethros the 19th" --difficulty hard --type combat

# Award after a combat encounter — exact CR calculation (preferred for standard combats):
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py award \
  --campaign <name> --characters "Max of Thraxx,Ethros the 19th" \
  --monsters "goblin:1/4:3,hobgoblin:1:1" --note "Ambush in the alley"

# Award for a qualifying non-combat encounter:
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py award \
  --campaign <name> --characters "Max of Thraxx,Ethros the 19th" --difficulty medium --type noncombat \
  --note "guild informant interrogation"
```

**Difficulty tiers:** `easy` `medium` `hard` `deadly`
**Encounter types:** `combat` `noncombat` (both use the same difficulty threshold table)
**Monster CR formats:** `1/4`, `0.25`, `1/2`, `0.5`, `1/8`, `0.125`, or integer (`1`, `5`, `10`)
**Monster count:** omit for 1 (e.g. `"dragon:10"`); explicit for groups (e.g. `"goblin:1/4:3"`)
**Monster multiplier** (applied automatically): ×1 (1), ×1.5 (2), ×2 (3–6), ×2.5 (7–10), ×3 (11–14), ×4 (15+)

`award` updates the character file XP field, flags LEVEL UP PENDING if a threshold is crossed, and pushes XP to the display via `push_stats.py`. The `--note` label prints to terminal only — not stored.

---

## Persistent Basic Encounter — `scripts/encounter_state.py`

For combatants with fixed weapon attacks, this wrapper calls `combat.py` for initiative and attacks while persisting order, HP and the roll history in `<campaign>/encounter.json`. `start` needs each combatant's `name`, `type` (`pc`/`npc`), `dex_mod`, `hp`, `ac`, plus either top-level `attack_bonus` and `damage` for one strike or an `attacks` array of 1–4 fixed profiles with `name`, `attack_bonus` and `damage` (for example `[{"name":"Claw","attack_bonus":4,"damage":"1d6+2"},{"name":"Bite","attack_bonus":3,"damage":"1d4+1"}]`).

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/encounter_state.py start --campaign $CAMP --combatants '[...]'
python3 ${CLAUDE_SKILL_DIR}/scripts/encounter_state.py status --campaign $CAMP
python3 ${CLAUDE_SKILL_DIR}/scripts/encounter_state.py attack --campaign $CAMP --token '<turn_token>' --target '<name>'
python3 ${CLAUDE_SKILL_DIR}/scripts/encounter_state.py pass --campaign $CAMP --token '<turn_token>'
python3 ${CLAUDE_SKILL_DIR}/scripts/encounter_state.py sync --campaign $CAMP
python3 ${CLAUDE_SKILL_DIR}/scripts/encounter_state.py checkpoint --campaign $CAMP
python3 ${CLAUDE_SKILL_DIR}/scripts/encounter_state.py archive --campaign $CAMP
```

Each action returns its event and the next token. For a multiattack, the next token stays with the same actor and advances to the next fixed profile; `pass` ends any remaining strikes. The PC sheets' HP lines and `state.md → ## Active Combat` are synced from encounter state; `sync` repeats the sheet write after an interrupted action. Completed rounds are appended once to `state.md → ## Recent Events` and `session-log.md`; `checkpoint --campaign $CAMP` repairs a missed write. `archive` works only after one side has been defeated and preserves the complete roll record. `--max-rounds N` on `start` pauses before round N+1; `resume --campaign $CAMP --max-rounds N` permits further play. The tool does not implement spells, reactions, Action Surge or conditional damage; use the normal rules for those.

---

## Inventory and Coins — `scripts/inventory.py`

The ledger owns each character's items and coins (`characters/<sheet>.inventory.json`) and the party's shared cargo (`party_inventory.json`). Every change rewrites the sheet's `## Equipment & Inventory` section and pushes to the display when it shows this campaign. Never edit that section by hand. Refusals exit 2 with one line and change nothing.

```bash
I="python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py"
$I show    --campaign $CAMP [--character Nella]          # every character without --character
$I buy     --campaign $CAMP --character Nella "Lantern" --price "5 gp" [--qty 1]
$I sell    --campaign $CAMP --character Elbane "Blanket" --price "24 sp" [--qty 4]
$I spend   --campaign $CAMP --character Elbane "3 sp"     # tolls, rent, bribes: makes change
$I earn    --campaign $CAMP --character Elbane "26 sp"
$I add     --campaign $CAMP --character Nella "Iron key" [--qty 1] [--category gear] [--notes "..."]
$I remove  --campaign $CAMP --character Nella "Torch" [--qty 1 | --all]
$I equip   --campaign $CAMP --character Nella "Shield"     # unequip to take off
$I cargo   --campaign $CAMP add|remove|show "Sack of salt" [--qty 3] [--notes "..."]
$I migrate --campaign $CAMP --character Nella [--write]    # prose section → ledger; dry run first
$I sync    --campaign $CAMP                                # regenerate sections and push
```

Categories: weapons, armour, gear, tools, consumables, trade goods, treasure (guessed when omitted). Amounts are written like `"2 gp 5 sp"`. `migrate` prints what it understood; show it to the player and add `--write` only after they confirm. It moves trade history (sales, debts paid) to `## Backstory & Notes` and keeps a backup under `backups/`.

---

## Combat Script — `scripts/combat.py`
```bash
# Roll initiative and print tracker
python3 ${CLAUDE_SKILL_DIR}/scripts/combat.py init '<JSON>'
# JSON: [{"name":"Flerb","dex_mod":0,"hp":12,"ac":16,"type":"pc"}, ...]

# Reprint tracker from saved state
python3 ${CLAUDE_SKILL_DIR}/scripts/combat.py tracker '<JSON>' <round_num>

# Resolve a single attack
python3 ${CLAUDE_SKILL_DIR}/scripts/combat.py attack --atk 4 --ac 15 --dmg 2d6+2
```
`init` outputs `STATE_JSON:` line — store in `state.md` under `## Active Combat` between turns.

---

## Character Script — `scripts/character.py`
```bash
# Full stat block from raw scores
python3 ${CLAUDE_SKILL_DIR}/scripts/character.py calc --class fighter --level 1 \
    STR=15 DEX=10 CON=15 INT=9 WIS=11 CHA=14 \
    --proficient STR CON Athletics Intimidation Perception Survival

# Level-up HP and bonus calculation
python3 ${CLAUDE_SKILL_DIR}/scripts/character.py levelup --class fighter --from 1 --hp-roll 7 --con-mod 2

# XP tracking
python3 ${CLAUDE_SKILL_DIR}/scripts/character.py xp --level 1 --gained 150
```

---

## Stats Display Script — `display/push_stats.py`
Pushes character and combat stats to the sidebar. Players merged by name; partial updates work.

```bash
# The whole party, read from the campaign's sheet files (use this at load and after sheet changes):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --party <campaign> [--replace-players]

# Full stats push by hand (only for a field the sheet doesn't hold):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --replace-players --json '{
  "players": [{
    "name": "Flerb", "race": "Tiefling", "class": "Fighter", "level": 1, "background": "Soldier",
    "hp": {"current": 12, "max": 12, "temp": 0},
    "xp": {"current": 220, "next": 300},
    "ac": 16, "initiative": "+0", "speed": 30,
    "hit_dice": {"remaining": 1, "max": 1, "die": "d10"},
    "second_wind": true,
    "ability_scores": {
      "str": {"score": 15, "mod": "+2"}, "dex": {"score": 10, "mod": "+0"},
      "con": {"score": 15, "mod": "+2"}, "int": {"score": 9, "mod": "-1"},
      "wis": {"score": 11, "mod": "+0"}, "cha": {"score": 14, "mod": "+2"}
    },
    "sheet": {
      "attacks": [
        {"name": "Longsword", "bonus": "+4", "damage": "1d8+2", "type": "Slashing", "notes": "Versatile (1d10)"},
        {"name": "Handaxe",   "bonus": "+4", "damage": "1d6+2", "type": "Slashing", "notes": "Thrown 20/60 ft"}
      ],
      "spells": null,
      "spells": null
    }
  }]
}' --from-sheets $CAMP

# sheet sub-keys: attacks, spells ({slots, save_dc, attack_bonus, cantrips, prepared} or null),
# features, inventory and ledger. --from-sheets fills the last three from the sheet files and
# ledgers (features grouped by class, species and background); do not write them by hand.
# sheet is optional — omit if you only need the stats sidebar without the full sheet modal

# Partial updates (use whenever values change mid-session):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --hp 7 12
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --xp 220 300
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --second-wind false

# Temp HP (Symbiotic Entity, Aid, etc.):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --temp-hp 8   # set
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --temp-hp 0   # clear

# Hit dice (short rest):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --hit-dice-use          # spend one
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --hit-dice-restore 2    # restore N

# Conditions — full replace:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --conditions "Poisoned,Frightened"
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --conditions ""          # clear all

# Conditions — granular (preferred mid-session):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --conditions-add "Poisoned"
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --conditions-remove "Poisoned"

# Concentration:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --concentrate "Bless"
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --concentrate ""        # clear

# Spell slots — full replace (on /dm:dnd load):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb \
  --spell-slots '{"1":{"used":1,"max":4},"2":{"used":0,"max":2}}'

# Spell slots — granular (preferred mid-session):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --slot-use 1      # expend one 1st-level slot
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --slot-restore 2  # restore one 2nd-level slot

# Items and coins: scripts/inventory.py (it pushes for you). Not --inventory-add.

# Faction standings (party-wide — REQUIRED at /dm:dnd load to show faction panel):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py \
  --factions '[{"name":"Pale Court","standing":"Allied"},{"name":"Watch","standing":"Neutral"}]'
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --factions '[]'   # clear all
# Optional "detail" on a faction or quest: shown when a player clicks it. Party-known facts only, no DM secrets.

# Combat turn order (on /dm:dnd combat start):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --turn-order \
  '{"order":["Goblin 1","Flerb","Goblin 2"],"current":"Goblin 1","round":1}'

# Advance turn pointer:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --turn-current "Flerb"

# New round:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --turn-current "Goblin 1" --turn-round 2

# Combat ended:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --turn-clear

# World time clock:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --world-time \
  '{"date":"19 Ashveil 1312 AR","day_name":"Moonday","time":"morning","season":"Long Hollow","weather":"calm"}'

# Clear display (use push_stats.py, NOT curl — raw curl lacks the auth token in LAN mode):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --clear

# Autorun cycle countdown (shown in party input panel):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --autorun-waiting true --autorun-cycle 60
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --autorun-waiting false   # hide after turn resolves

# N-player threshold — auto-fire when N players (not all) are ready:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --autorun-threshold 2   # fire when 2 ready
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --autorun-threshold 0   # reset to player count
```

**Player input queue — `display/check_input.py`:**
```bash
# Called at the start of each turn BEFORE processing the player's message.
# Drains any actions queued from the display companion (e.g. iPad) and prints them.
# Output: "[Max of Thraxx]: I draw my rapier" — empty if nothing queued. Clears the display indicator.
python3 ${CLAUDE_SKILL_DIR}/display/check_input.py
```

If `check_input.py` returns output, prepend it to the player's terminal input when forming the turn:
- Only queued input: treat as the full player action this turn
- Queued input + terminal input: merge as `[Character]: <queued>\n[Character]: <terminal>`
- Empty queue: proceed as normal (use only terminal input)

---

**When to push stats:**
- `/dm:dnd load` → `--replace-players --json` (full stats) + `--spell-slots` + `--world-time` + `--factions`
- HP change → `--player NAME --hp <current> <max>`
- Temp HP gained/lost → `--player NAME --temp-hp N` (0 to clear)
- XP awarded → `--player NAME --xp <current> <next>`
- Second Wind used/recovered → `--player NAME --second-wind false/true`
- Hit die spent → `--player NAME --hit-dice-use`; restored → `--hit-dice-restore N`
- Spell slot used → `--player NAME --slot-use <level>`; restored → `--slot-restore <level>`
- Condition gained → `--player NAME --conditions-add "Name"`; removed → `--conditions-remove "Name"`
- Concentration started → `--player NAME --concentrate "Spell"`; ended → `--concentrate ""`
- Item or coin change → `scripts/inventory.py` (it updates the sheet and the display)
- Timed effect starts → `--effect-start "NAME:SPELL:DURATION[:conc]"` bundled with narration send
- Timed effect ends → `--effect-end "NAME:SPELL"` bundled with narration send
- Faction standing changes → `--factions '[...]'` (full replace)
- Combat start → `--turn-order`; each turn → `--turn-current`; end → `--turn-clear`
- Level up → push updated full stats
- Long rest → restore HP, hit dice, spell slots, second wind; push `--world-time` with updated time
- Any rest or time advance → push `--world-time`

**Keep the clock honest.** The world clock is continuity, not decoration. Narrate consistently with the time you last pushed, and advance it *deliberately* when an action costs time — a brief exchange is a few minutes, a search or a shopping trip is longer, a rest or a journey longer still. Never let the time of day drift on its own or silently reset between scenes. If the clock and the fiction ever disagree, reconcile it to the truth with a fresh `--world-time` push rather than compounding the error; setting it *backward* to the correct time is fine and undoes nothing, since timed effects run on their own round/minute/hour durations in `tracker.py`, independent of the wall clock.

---

## Tracker Script — `scripts/tracker.py`
Tracks conditions, concentration, timed effects, and death saves. State persists at `~/.claude/dnd/campaigns/<name>/tracker.json`.

```bash
CAMP=my-campaign

# Timed effects — duration: 10r (rounds), 60m (minutes), 8h (hours), indef
# Append 'conc' to mark as concentration (auto-sets concentration field)
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP effect start "Max of Thraxx" "Web" 10r conc
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP effect start "Ethros the 19th" "Disguise Self" 1h
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP effect start "Ethros the 19th" "Hunter's Mark" indef
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP effect end   "Max of Thraxx" "Web"   # narrative end (broken/dispelled)
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP effect tick  "Max of Thraxx"         # call on actor's turn — decrements rounds, prints expiry

# Conditions
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP condition add "Ethros the 19th" poisoned
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP condition remove "Ethros the 19th" poisoned
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP condition clear "Ethros the 19th"

# Concentration (auto-clears previous if switching spells)
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP concentrate "Max of Thraxx" "Bless"
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP concentrate "Max of Thraxx" break

# Death saves
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP saves "Ethros the 19th" success
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP saves "Ethros the 19th" failure
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP saves "Ethros the 19th" stable
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP saves "Ethros the 19th" reset

# Status / clear
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP status
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP status "Ethros the 19th"
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP clear           # conditions + concentration + effects
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP clear --all     # also clears death saves
```

**When to run:** condition applied/removed; caster begins/loses concentration (immediately, not end of turn); PC drops to 0 HP; each death save rolled; end of encounter → `clear`.

---

## Calendar Script — `scripts/calendar.py`
```bash
# One-time setup (run during /dm:dnd new):
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP init \
    --date "15 Harvestmoon 1247" \
    --time "morning" \
    --months "Frostfall,Deepwinter,Thawmonth,Seedtime,Bloomtide,Highsun,Harvestmoon,Duskfall" \
    --month-length 30 \
    --day-names "Sunday,Moonday,Ironday,Windday,Earthday,Fireday,Starday"

# Time advancement
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP advance 8 hours
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP advance 2 days            # travel: rolls 2 road days
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP advance 3 days --no-road  # time passes, no travel
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP rest short   # +1 hour
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP rest long    # +8 hours

# Query / manual set
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP now
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP set "22 Harvestmoon 1247" evening
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP time night
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP events
```

**When to run:** after every rest; after significant travel or time skip; when manually updating `state.md` date — use `calendar.py set` to keep them in sync.

**Road days.** In an improvised campaign (`type: dynamic` or `type: sandbox` in `## Campaign Arc`), `advance` also prints one road-day roll for each whole day that passes (`oracle.py road`, below), so travel events arrive without a separate call. Prebuilt (`structured`) campaigns get nothing extra. When days pass without travel (waiting in town, recovering, a week of downtime), add `--no-road`. Rests use `rest short` / `rest long`, which never roll the road. Example:

```
  +3 days → Moonday, 18 Duskfall 1247 — morning (hour 8)
On the road: one roll per travel day (improvised campaign). Not travelling? Ignore these, and use --no-road for rests, waiting in town or downtime.
Day 1 of 3 (16 Duskfall 1247): discovery. Fresh tracks of several riders leaving the road for the woods. Investigating costs time; ignoring it may cost later.
Day 2 of 3 (17 Duskfall 1247): quiet. Cart ruts, a milestone with a worn name, smoke from a farmhouse chimney. Give it one vivid line and move on.
Day 3 of 3 (18 Duskfall 1247): danger. Wolves circling the camp at dusk, bold with hunger. Telegraph the threat, and let talk, flight or a fight all work.
    Encounter budget (medium for 2 characters of level 1): about 100 XP. Roughly 1 × CR 1/4 (wolf), 2 × CR 1/8 (bandit) or 4 × CR 0 (jackal).
```

---

## Campaign Search — `scripts/campaign_search.py`
Keyword search across campaign files. Use this **before** loading full files into context when looking up a specific past event, NPC detail, or plot thread.

```bash
CAMP=my-campaign

# Search all default files (state, log, archive, world, npcs):
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_search.py -c $CAMP Lasswater

# Narrow to specific files:
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_search.py -c $CAMP "merchant letter" --files log,archive

# Multi-keyword AND search:
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_search.py -c $CAMP VARETH Kel

# More context lines around each match:
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_search.py -c $CAMP Harwick -C 6
```

File keys: `state`, `log`, `archive`, `world`, `seeds`, `npcs`, `npcsfull`
Default files searched: state, log, archive, world, npcs

**When to use:** Any time a player asks about a past event, NPC detail, location, or plot thread that may not be in active context. Run this first — only escalate to a full `Read` if the search returns insufficient context.

---

## Session Recap — `scripts/session_recap.py`

Deterministic state-diff between two character snapshots. Computes the mechanical change set (HP/temp/level/hit dice/death saves/conditions/concentration/exhaustion/inspiration/spell slots) from data so narration never recomputes it — recaps are the single thing an LLM is most likely to hallucinate. Reads `<campaign>/characters/*.md` and merges live `tracker.json` conditions/concentration. Zero LLM calls.

```bash
CAMP=my-campaign

# Snapshot the party now — sets the baseline (writes to <campaign>/.recap/,
# rolling last → prev). Run this at session START (e.g. /dm:dnd load) so there
# is a baseline to diff against later.
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py snapshot --campaign $CAMP

# Diff the baseline against current state → one-paragraph summary, then ADVANCE
# the baseline to "now" so the next diff chains from here. Run at /dm:dnd save
# (end of session) for a since-start recap, or each turn for a since-last-turn
# recap — either way it advances, so consecutive diffs never re-report old deltas.
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py diff --campaign $CAMP
# → "Aldric: took 18 damage (30→12 HP); gained Poisoned; spent 2 level 1 slots."

# Same comparison without moving the baseline (ad-hoc "what changed so far?"):
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py diff --campaign $CAMP --no-roll

# Structured change list instead of prose:
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py diff --campaign $CAMP --json

# Diff two snapshot files directly (no campaign lookup):
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py diff-files before.json after.json
```

---

## Turn log and NPC records — `display/records.py`, `display/npc_describe.py`

Nothing to run: `send.py` keeps both after each send reaches the display. A block the display never got is not recorded. A failure prints one `send.py: turn log or NPC record not written` line and never fails the send.

**`<campaign>/turn-log.md`**, written only by `send.py` (never edit it):

    # Turn log

    ## Session 7 · 2026-11-14

    ### 21:04 · Brackenford toll gate · 18 Duskfall 1247

    **Everybody:** We hand over the satchel.

    **DM:** The clerk breaks the seal.

    **Hessa Varn:** "Twenty silver, or the wagon stays."

- A `## Session N · <date>` section starts after the portal's Save & close, or after three hours without a send. N is one more than the newest `## Session N` in `session-log.md`, `session-log-archive.md` or `state.md`. At Save & close the portal's notes helper (`display/session_summary.py`) writes `## Session N` in `session-log.md` from every section numbered N, so the next session's number follows on.
- One `###` entry per send: the real time, the place (the last `--scene` place, else `state.md`'s Location) and the in-world date.
- One paragraph per block, word for word: `DM` (narration), `DM hint` (tutor), `Dice`, `XP`, `Inspiration`, `Milestone`, or the player, action or NPC name. A line starting with `#` inside a block is written as `\#`.
- A replay is skipped: a send whose blocks were all logged in the last 40 and are each 40 characters or longer. Awards and short lines are always logged. A long block cut short by a failed post is logged as far as it was shown.
- Read the newest session with `grep -n '^## Session' turn-log.md`, then `sed -n` from the last match; never the whole file.

**NPC records.** For each `@npc NAME` speaker (or `--npc NAME`) whose name starts with a capital, `send.py` adds to their entry in `npcs.md` (or `npcs-full.md` if the entry lives there; a first name alone finds the one entry it starts):
- `- **Met:** <place>, session N (<in-world date>)`, the first time only.
- `- **Voice:** <id>`, the voice the display uses (`cast.json`), else the marker's.
- `- **As seen:** <phrases>`: when the entry has no look line (`As seen`, `Look`, `Appearance`, `Description`), the helper model chosen in the portal (`lore_sync.py`: Luna by default, off with `DND_LORE_SYNC=0`) copies up to three phrases about that person from the narration, in the background. Phrases not found word for word in the narration are dropped, and nothing is written when the narration said nothing. Phrases must match whole words, so "scarred" never comes from "unscarred". It is added on the next send (kept for a later send if that write fails), at most three tries per person; failures go to `runtime/npc_describe.log`.

The scripts only ever add a line the entry lacks. They never change or remove one, so personality, motives and anything else the DM wrote stand. `oracle.py` and `send.py` share one lock on `npcs.md`.

---

## Where Play Stopped — `scripts/stop_point.py`

Writes and shows `<campaign>/session-tail.md`: one line on where play stopped, the open question or pending roll, and the last eight exchanges word for word. Every portal save requires a fresh one, and a resumed DM reads it first. Without one, `show` prints the last eight entries of the display's `session_tail.json`.

```bash
echo '[{"who":"Players","text":"We ask Rosa about the ledger."},{"who":"DM","text":"Rosa slides it across. Wren, roll Insight."}]' |
  python3 ${CLAUDE_SKILL_DIR}/scripts/stop_point.py write --campaign $CAMP \
    --where "Rosa's bakery, Brinewick, morning of 16 Leaffall; Rosa has just shown the ledger." \
    --waiting "Wren's Insight roll on Rosa (DC 13)"
python3 ${CLAUDE_SKILL_DIR}/scripts/stop_point.py show --campaign $CAMP
```

---

## Oracle — `scripts/oracle.py`

Dice-driven solo/improv oracles (Mythic chaos factor, Ironsworn yes/no, Random Event Focus, scene-meaning word pairs). Keeps pacing transparent and rollable instead of invented. Rolls are stdlib-random and seedable (`--seed N`). The chaos factor persists in `state.md → ## Session Flags` as `chaos_factor: N`. Zero LLM calls.

```bash
CAMP=my-campaign

# Chaos factor (1-9): show / set / adjust (persisted to state.md)
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py chaos --campaign $CAMP
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py chaos set --campaign $CAMP --value 7
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py chaos adjust --campaign $CAMP --pc-lost

# Improvised campaigns only (Standard 16): a new NPC's temperament, and what each travel day brings.
# npc with --campaign and --name is seeded from the name (same name, same person) and saves to npcs.md.
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py npc --campaign $CAMP --name "Bera Holt" --trader
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py npc note --campaign $CAMP --name "Bera Holt" "Sold 4 blankets at 18. Thinks they drive a hard bargain."
# road prints a skip notice for prebuilt (structured) campaigns. calendar.py advance runs it for you.
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py road --campaign $CAMP --days 3

# Yes/no oracle — likelihood + chaos modifier → verdict + d100
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py ask --likelihood likely --campaign $CAMP
# → "NO-BUT  (d100=82, likelihood=likely, chaos=8)"

# Random Event Focus (d100 → direction label)
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py event

# Scene-meaning word pair (action / subject)
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py scene
```

Likelihoods: `sure-thing`, `likely`, `50/50`, `unlikely`, `no-way`. Verdict suffixes: `-and` (extreme, on doubles), `-but` (qualified, near threshold).

**`npc`.** Rolls a temperament tier (about 60% ordinary, 30% colourful, 6% memorable, 4% troubled), a current want (about half ask something of the party, marked "an ask") and, with `--trader`, a trading style. With `--campaign` and `--name` it writes `- **Temperament:**`, `- **Wants now:**` and `- **Trades:**` lines into that NPC's entry in `npcs.md` (a `###` heading starting with the name), adding the entry if it is missing; running it again rewrites the same values. `npc note` appends `- Party note (<in-world date>): <line>` to the end of the entry: one line after a scene, on how it went and what the NPC now thinks of the party.

**`road`.** One roll per travel day: quiet, traveller, obstacle, discovery, danger or story, each with several examples. With `--campaign` it reads `state.md`:
- `pacing` in `## Session Flags` weights the table: `adventure` has about half the quiet days and over three times the story days of `mixed`; `downtime` has more quiet days and travellers, and less danger.
- Story days use `## Next Hooks` bullets and `## Clocks` lines (`- <date>: <what> (seen by: <how>)`) when present; about half of traveller days bring a clock's news or a hook.
- `road_log.json` in the campaign folder keeps the last 10 days, so an example, hook or clock is not reused while it is in the log (a hook used on a story day is not repeated by a traveller either), and the same eventful kind never comes twice in a row.
- Danger days add an encounter budget for the party (size and average level from `party_stats.json`, else `characters/*.md`), at the tier set by the `difficulty` dial (`standard` = medium).
- `road_next.json` holds a day the turn card's journey prep has already rolled (see Turn prep below), kept under the in-world date it was rolled on. The next road roll on that date, from `calendar.py advance` or `oracle.py road`, uses it as day 1 and deletes it once the log holds it. It is dropped and rolled fresh once the date has moved on, or when the hook or clock it was built on has left `state.md`. Preparing and using it share a lock (`road_next.lock`) with `calendar.py advance`, so the two never cross.

---

## Turn card and arc — `scripts/arc.py`

**The turn card.** In portal play every party reply arrives with one block in front of it: `[[Turn card, hidden from players: … ]]`. The portal builds it from the campaign files (`scripts/turn_card.py` at the repository root), so it is as current as `state.md`. It holds, when the files have them: where and when the party is; the current arc beat and its `steering_notes` (or a structured campaign's chapter and next key beat); the top two `## Next Hooks`; any `## Clocks` entry due within two in-world days or overdue; the `Trade policy:` pinned fact; the people present, from `npcs.md`; the table's narration length and campaign roll mode; and three standing rules. Per-character overrides from the display's Rolls toggle follow the card as `[[<Character> roll mode: auto|players]]`, as `check_input.py` prints them; the portal does not run `check_input.py`, so this is how they arrive. Read it, act on it, and never show it to the players. Keep `## Next Hooks`, `## Clocks`, the trade policy and the arc current, because the card is only as good as `state.md`.

When the story has sat on one beat for too long (`steer_after: N` in Session Flags, otherwise 4, 6 or 10 turns for `spotlight: dm_led`, `balanced` or `player_led`; `steer_after: 0` turns it off), the beat line becomes **DM steering, mandatory this turn**. The first nudge says what to bring into the scene now. The second nudge for the same beat asks whether the beat has already landed: if it has, run `arc.py advance`; if not, run `arc.py revise` with a pressure that fits where the party is. Turns during active combat, dice-only replies and out-of-character notes (`OOC`, `((…))`, `//`) do not count. The count restarts when the beat, its pressure or the steering notes change.

```bash
# What is the arc doing now?
python3 ${CLAUDE_SKILL_DIR}/scripts/arc.py status  --campaign <name>
# The current beat's change has landed: mark it complete, move to the next beat and act
python3 ${CLAUDE_SKILL_DIR}/scripts/arc.py advance --campaign <name> [--beat 1b]
# The beat no longer fits where the party is: give it a new pressure, change or steering
python3 ${CLAUDE_SKILL_DIR}/scripts/arc.py revise  --campaign <name> --beat 1b --pressure "Piers panics as the auditor arrives on 18 Duskfall" [--what-changes "..."] [--steering "..."]
```

`advance` clears `steering_notes`, so write fresh steering for the new beat with `revise --steering`. For a structured campaign, `advance` ticks the chapter's next key beat off `outstanding_beats`; moving to a new chapter still means reading `arc.md`. `revise` refuses completed beats and logs each change in `revision_log`. Both rewrite only the YAML block under `## Campaign Arc` (comments inside it are dropped) and leave the rest of `state.md` as it was.

**Turn prep** (`scripts/turn_prep.py`). When the party's reply plainly starts a fight, a trade or a journey, the card gets one `Prepared …` line, worked out by script while the card is built: for a fight, the SRD stat block (AC, HP, speed, initiative, CR and attacks) of up to two creatures the party names; for a trade, the SRD list prices of up to four items named, and each character's purse from the inventory ledger; for a journey in an improvised campaign, the next road day, rolled once and kept (see `road` above). Only one prep a turn, fight before trade before journey, and nothing when no creature, item or improvised road applies. It supplies numbers only. Notes in `[[…]]` never trigger it. To see what it would print: `python3 ${CLAUDE_SKILL_DIR}/scripts/turn_prep.py <campaign-dir> "<party input>"` (a journey check keeps the roll it makes).

**`## Clocks`** in `state.md`: one bullet per dated event, `- 18 Duskfall 1247: <what happens> (seen by: <how the party could notice>)`. Dates use the campaign calendar's month names (`calendar.json`); a clock whose date can't be read is left off the card. Remove a clock once it has played out.

---

## Deterministic Graph Extraction — `scripts/graph_extract_deterministic.py`

Zero-LLM relationship extractor. Pattern-matches session-log sentences against the bundled verb-table seed (`data/graph/verb_table_seed.yaml`) and emits typed edge proposals in the exact shape `campaign_graph.py` consumes. ~50% recall (clean subject-verb-object only), ~95% precision, no Claude API call. Usually driven through `campaign_graph.py extract --deterministic` rather than directly:

```bash
CAMP=my-campaign

# Propose edges (stdout), no writes:
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_graph.py extract --campaign $CAMP --deterministic

# One-shot auto-apply high-confidence proposals into graph.json (idempotent):
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_graph.py extract --campaign $CAMP \
    --deterministic --apply --min-confidence high
```

---

## Data Commands — `scripts/sync_srd.py`, `scripts/build_srd.py`, and `scripts/lookup.py`

Dataset is bundled at `${CLAUDE_SKILL_DIR}/data/dnd5e_srd.json`. No runtime download required.

```bash
# Check / rebuild dataset (only needed when upstream sources update):
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_srd.py             # rebuild if 5e-bits or FoundryVTT has new commits
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_srd.py --check     # check upstream SHAs, don't rebuild
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_srd.py --force     # always rebuild
python3 ${CLAUDE_SKILL_DIR}/scripts/build_srd.py --status   # show current dataset metadata

# Lookup during play (CLI):
python3 ${CLAUDE_SKILL_DIR}/scripts/lookup.py spell "fireball"
python3 ${CLAUDE_SKILL_DIR}/scripts/lookup.py item "cloak of protection"
python3 ${CLAUDE_SKILL_DIR}/scripts/lookup.py feature "sneak attack"
python3 ${CLAUDE_SKILL_DIR}/scripts/lookup.py condition "poisoned"
python3 ${CLAUDE_SKILL_DIR}/scripts/lookup.py monster "goblin"
python3 ${CLAUDE_SKILL_DIR}/scripts/lookup.py monster "dragon" --all   # all fuzzy matches

# Programmatic (used by display companion /srd-lookup endpoint):
from lookup import lookup, lookup_record, lookup_with_level, suggest
lookup("fireball", category="spell")                  # → formatted string
lookup_with_level("sneak attack", category="feature", level=3)  # → level-resolved string
suggest("poisonned", category="condition")            # → [("Poisoned", "conditions"), ...]
```

**Did-you-mean recovery.** A mistyped name doesn't dead-end. When a lookup misses, the CLI prints a `Did you mean: …?` line and the display's SRD modal offers tappable near-miss chips — both powered by `suggest()`, which fuzzy-matches the query against real names (`poisonned` → Poisoned, `fireballl` → Fireball, `gobblin` → Goblin). Suggestions respect the category when one is given, and search all categories otherwise. Use the suggested name rather than guessing at a spelling.

**When to use:** combat (monster stat blocks before using them); spellcasting (range, components, duration, at-higher-levels); conditions (rule text before applying); loot and equipment; NPC generation (monster stat block as mechanical base). The display companion's character sheet modal handles lookups automatically during play — these CLI calls are for DM reference outside the UI.

---

## Display Companion Setup (one-time)

```bash
cd ${CLAUDE_SKILL_DIR}/display
pip3 install -r requirements.txt
```

```
Terminal (run claude directly — no wrapper needed)
    ↓ send.py calls per narration block / dice roll / stat change
Flask on https://localhost:5001 (dnd-display-app.py — HTTPS, self-signed cert)
    ↓ Server-Sent Events
Browser tab → Chromecast → TV
```

**Start the display:**
```bash
bash ${CLAUDE_SKILL_DIR}/display/start-display.sh          # localhost
bash ${CLAUDE_SKILL_DIR}/display/start-display.sh --lan    # LAN mode (phones, tablets)
open https://localhost:5001                                  # open browser before /dm:dnd load
```

`start-display.sh` always force-kills any previous instance before starting — no manual pre-kill needed.

**Load a campaign:**
```
/dm:dnd load <campaign-name>   # skill auto-detects running display, pushes party stats
```

The DM skill sends each narration block, dice result, and stat update via `send.py` calls (see Active DM Mode in SKILL.md for full send sequence and stat flag reference).

Open the browser tab and Chromecast it *before* running `/dm:dnd load` so the browser is connected when the opening narration streams in. The display buffers the last 60 chunks and replays them to reconnecting browsers.

**Scene cue.** Send one when the party arrives somewhere new, or when a place's mood truly changes. Bundle it with the first narration there:

```bash
python3 ${CLAUDE_SKILL_DIR}/display/send.py --scene "Saltmere, Meren's bakery: haggling over flour by the ovens" << 'DNDEND'
Warm bread and woodsmoke fill the little shop.
DNDEND
```

Write the place, a colon, then what the party sees happening. Add `--mood calm|curious|ominous|tense|warm` only when the moment needs a different feel from the place. There is no need to read `/health` first: `send.py` reads the current scene and the campaign's `world.md`, works out the rest, and prints one line saying what it chose.

- **Key:** taken from the place alone, so the same place always gets the same key. A cue for the place already on screen changes nothing and prints `scene unchanged`, unless `--mood` or the world era differs.
- **Setting:** keyword mapping of the place to one of the 18 broad settings (`inn` to tavern, `bakery` to city, `cavern` to cave). If the place has no keyword, the activity words are tried, then the current setting, then `city`.
- **Place theme:** a place named in the world notes takes its saved path and mood. Two formats are read: `Town: [{"name":"Town","mood":"tense"}]. Gate: child {"name":"Gate"}` lines, and `Scene theme: Region (warm) → Place (curious)` lines. A named place with extra words (`Saltmere docks`) becomes a child of that place. An unknown place becomes a child of the current outer region.
- **Era:** an `Era:` or `Established era:` line in the world notes, else a `Setting type:` naming medieval, renaissance, ancient, modern or futuristic, else the current scene's era, else medieval.
- **Mood:** `--mood`, else the nearest saved place mood, else the setting's usual tone.
- **Prompts:** the music and the background illustration are both written by `display/scene_engine.py` from that one scene.

To give a new place a lasting tone for revisits, add a line under `## Place Themes` in `world.md`, such as `Scene theme: Greenwood (warm) → Clearing Hut`. Don't change scenes for a mentioned destination, ordinary dialogue or every paragraph. For combat, send `--music-event combat-start` with the first fighting narration and `--music-event combat-end` with the first narration after it; the scene survives combat.

**Long-form scene flags (reference).** Older prompts may still send the full cue. It still works, but cannot be mixed with `--scene`:

`--music-event scene --music-setting <setting> [--music-mood <mood>] --scene-key <slug> --scene-label <text> --scene-description <text> [--scene-era <era>] [--scene-theme '<JSON path>']`

Key, label and description go together. The key is an ASCII slug of up to 64 characters, the label up to 120, the description up to 800 and the era up to 100. The theme is an outer-to-inner JSON array of up to eight `{"name": …, "mood": …}` places, with mood optional. Omitted era, theme or mood inherits from the current scene, and `--scene-theme '[]'` clears the path. The JSON form puts the same fields in `music.scene`, beside `event`, `id`, `setting` and `mood`. The same key, setting, theme, era and mood keep the current media. A change to any of them makes fresh music and a fresh illustration.

**Automatic detection:** with no named scene, keyword scanning of narration picks a broad setting, and ordinary narration starts scene music when music is idle. Setup questions, character speech and dice results never change the scene. When configured, the local illustration queues after the scene's intro and loop finish.

**Sound effects:** `audio.py` scans narration for optional one-shot effects. Character names are excluded from trigger matching. These effects are separate from the generated intro and music loop. The display has separate Sound Effects and Music toggles.

---

## Continuity Autosave — `scripts/autosave_checkpoint.py`, `scripts/install_autosave_hook.py`

Behind-the-scenes continuity checkpoint for long sessions, so a context compaction never loses the player's place. Two layers; see the *Continuity micro-save* rule in SKILL.md and the `/dm:dnd autosave` command.

```bash
# Opt-in: register the Stop hook (writes ~/.claude/settings.json, idempotent)
python3 ${CLAUDE_SKILL_DIR}/scripts/install_autosave_hook.py
python3 ${CLAUDE_SKILL_DIR}/scripts/install_autosave_hook.py --uninstall
python3 ${CLAUDE_SKILL_DIR}/scripts/install_autosave_hook.py --status

# The hook target (also runnable by hand to force a snapshot or inspect state)
python3 ${CLAUDE_SKILL_DIR}/scripts/autosave_checkpoint.py --status
python3 ${CLAUDE_SKILL_DIR}/scripts/autosave_checkpoint.py --campaign <name> --snapshot-only
```

`autosave_checkpoint.py` runs as a Claude Code **Stop hook** (after each turn). It reads the active campaign from `<runtime-dir>/active-campaign.json` (written at `/dm:dnd load`) and the `autosave` flag from that campaign's `state.md`. It **no-ops** when no campaign is active (e.g. a non-D&D session), when `autosave: off`, or when already inside a hook-driven continuation. Every turn it snapshots `state.md` to the runtime dir; every N turns (default 10, `DND_AUTOSAVE_EVERY` to override) it emits a Stop-hook `block` decision that prompts the DM to flush continuity before yielding. The hook is **opt-in** — the in-model micro-save cadence works without it.

**When to use:** offer `install_autosave_hook.py` to players running long imported modules who hit compaction mid-session. The flag toggle (`/dm:dnd autosave on|off`) is the in-session control.

## Lazy Corpus — `scripts/corpus_check.py`

Imported (structured) campaigns keep the full module text as a lazily-loaded reference layer instead of inlining it. Layout:

```
<campaign>/
  world.md           # load-time core (Foundations, Three Truths, factions)
  world-nodes.md     # lazy: full Quest Seed Bank + Adventure Nodes (per-act read)
  arc.md             # lazy: full act/chapter tree (state.md holds current+next only)
  source-index.md    # chapter-id -> source file -> one-line scope
  source/<id>.md     # lazy: one file per chapter, the module's source text
```

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/corpus_check.py --campaign <name>
```

Validates that every chapter id in `source-index.md` has a matching `source/<id>.md` (and vice-versa) and that `arc.md` exists. Run it at the end of `/dm:dnd import`. A campaign with no `source/` layer (dynamic, sandbox, or a pre-v2.2.0 import) is reported as a clean no-op — nothing to validate, and its load path is unchanged.
