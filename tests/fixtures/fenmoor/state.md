# Campaign: Fenmoor
**Created:** 2026-11-01  **Last session:** 2026-11-02  **Session count:** 1  **Ruleset:** 2024

## Current Situation
- **Location:** Reedhythe, Effie Cobb’s candle shop
- **In-world date/time:** 9 Mistmonth 1312, morning
- **Party:** Corin Ashby — Halfling Bard 1 | HP 9/9 | AC 13 | Bardic Inspiration 3/3 | spell slots 2/2; Wren Talley — Dwarf Fighter 1 | HP 13/13 | AC 16 | Second Wind 2/2
- **Party status:** Inside the candle shop, warming up after a cold crossing; the punt is tied up at the fish quay with nothing aboard.
- **Trade status:** The cousins sold a basket of smoked eel to Effie for 12 sp and bought a coil of tarred line for 4 sp. Corin carries 14 gp 3 sp 6 cp. The eel was paid for in full before they left Gullmere; the tarred line has not been checked for rot.

## Pinned Facts
- Trade policy: aim for a 25% margin; test goods before paying; Wren does the haggling and Corin keeps the tally.
- Grim fen folklore with room for warmth. Horror stays off-screen, no cruelty for its own sake; choices stick and kindness is remembered.
- Corin and Wren are cousins who share a hired punt and split every coin; Wren is a player character, controlled by her player. Favours owed and the town's opinion of them carry from session to session.

## World State
- **In-world date:** 9 Mistmonth 1312
- **Season:** autumn  **Weather:** fog on the mere
- **Threat arc stage:** 1 — Now
- **Faction states:** The Lamplighters' Guild keeps the beacons and the lamp-oil stores; the Weir Union fishes the mere by night and is losing money while the Gullmere beacon stays dark.

## Active Quests
- Bring Effie's empty candle crates back to her by the next market day.
- Learn why the Gullmere beacon is dark before risking a night crossing.

## Open Threads & Rumours
- The Gullmere beacon has been dark for three nights. Its keeper will say only that the last oil delivery was "wrong".

## Next Hooks
- Reedhythe: Juniper Vane's apprentice is buying every candle in Effie's shop and paying in Heron Isle silver, too much and too fast, and won't say who the candles are for.
- The causeway: a Weir Union eel barge has run onto the mudflats in the fog; its crew offer a share of the catch to be towed off before the tide turns, and one swears the beacon oil "stank of fish" (beat 1b).
- Gullmere: Ned Harrow will lend the cousins his fast skiff for the season if they keep the beacon lit for one night themselves.

## Faction Moves
- 9 Mistmonth: the Lamplighters' Guild has posted a notice in Reedhythe that beacon oil is rationed until the spring barge. The Weir Union has voted to run the night crossing anyway.

## Clocks
- 11 Mistmonth 1312: the Weir Union's eel barges try the night crossing to Gullmere, beacon or no beacon (seen by: punts loading eel traps at the Reedhythe quay; bargemen arguing about the dark)
- 14 Mistmonth 1312: the Heron Isle fever house burns its last lamp oil (seen by: a lone rowboat crossing from the Isle at dusk; a chalked plea on the quay post)

## Recent Events
- Session 1 (played 1 to 2 Nov): the cousins poled Ned Harrow's smoked eel across the mere, waited out a fog bank on the causeway and sold the catch to Effie in Reedhythe. They spend the morning of 9 Mistmonth in her shop with an empty punt.

## Active Combat
None.

## Live State Flags
- The punt is empty and tied up at the Reedhythe fish quay.
- Effie has paid for the eel; nobody owes the cousins money and they owe nobody.
- Maud is at the Gullmere beacon; Ned is out on the weirs.

## Campaign Arc
```yaml
type: dynamic
arc_number: 1
theme: A town shows what it values by whom it keeps in the light.
resolution: If the cousins succeed, Fenmoor keeps both its beacon and its sick provided for, through bargains its people can live with.
acts:
- act: 1
  title: Lamps Gone Dark
  beats:
  - id: 1a
    label: The Unlit Tower
    what_changes: The cousins' punt trade is tied to the dark beacon and the Weir Union's lost night runs
    world_pressure: Maud keeps the beacon shuttered while Ned's eel barges sit idle
    status: complete
  - id: 1b
    label: Fish Oil and Smoke
    what_changes: The dark beacon proves to be someone's choice, not bad luck
    world_pressure: Silas Crane, the Guild's oil factor in Reedhythe, scrambles as the Weir Union barges risk the night crossing on 11 Mistmonth; the refilled casks and the oil shipped to Heron Isle start to surface, and both Maud and Ned want the cousins on their side.
    status: current
- act: 2
  title: The Fever Isle
  beats:
  - id: 2a
    label: Where the Oil Went
    what_changes: The cousins learn who is kept alive by the missing oil
    world_pressure: The Guild and the Union each want the Isle's supply cut or kept
    status: pending
  - id: 2b
    label: A Night Without Light
    what_changes: A crossing goes wrong in the dark and someone the cousins know is hurt
    world_pressure: Fog and a frightened town turn against the Isle
    status: pending
- act: 3
  title: Bright Water
  beats:
  - id: 3a
    label: The Sluice Moot
    what_changes: The cousins' word at the moot decides who carries the cost of the beacon and of the sick
    world_pressure: The Guild and the Union bring rival terms to the moot
    status: pending
  - id: 3b
    label: Morning on the Mere
    what_changes: What was promised in the fog turns into friendships, grudges and a changed fen
    world_pressure: Old grudges and new friends test every promise
    status: pending
current_act: 1
current_beat: 1b
outstanding_beats:
- 1b
- 2a
- 2b
- 3a
- 3b
steering_notes: Steer the cousins to Gullmere with a reason (the candle buyer, the stranded barge). Let them smell the bad oil and watch the night crossing go ahead without a light. Clues they can find on their own are the cask brands, the Isle's receipts and Silas's manifest. Nobody here is a villain.
revision_log:
- '2 Nov 2026: beat 1a done (the punt trade is tied to the dark beacon); 1b now names Silas and the crossing date.'
```

## Session Flags
tts_voice: bm_george
roll_mode: players
autosave: on
spotlight: dm_led
pacing: adventure

## DM Style Notes
Fog, small kindnesses and hard bargains. Clues sit where the players can find them and every problem has more than one way through. Players roll their own checks in the dice tray; the DM rolls for NPCs in the open. Give each turn one person or one choice, and finish on something that pulls the story forward: a question with a price, someone who needs something, or the weather turning, never just "what would you like to do?". Wave through small purchases unless the deal itself is the beat; travel goes day by day across the mere. Wren's player decides what Wren does.

## DM Notes (hidden from players)
Silas Crane, the Guild's oil factor, has been sending the clean beacon oil to Juniper Vane's fever house on Heron Isle, where the lamps must burn all night, and refilling the beacon casks with rendered fish oil that smokes and gutters. He believes he is saving lives and has told no one. Maud knows the oil is bad but not why. Clues: the cask brands, Juniper's oil receipts, Silas's manifest.
