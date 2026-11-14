# Travel, the road and errands (improvised campaigns)

Read once when the party sets off, travels or passes time.

## Moving
- Travel always goes through `calendar.py -c X advance N days` (or `hours`), before the narration call. It prints one road day per day passed. Give a quiet day one line of colour. Stop at anything else, present it, and let the party choose. Never compress a journey into one sentence.
- Time passing without travel (waiting in town, downtime) adds `--no-road`. A long rest is `calendar.py -c X rest long`, a short one `rest short`; neither rolls the road.
- A new place, or a place whose mood truly changes, gets `--scene "<place>: <what is happening>"` on that narration (see scene-media.md).
- Reaching a new town by a played road earns milestone XP (`--difficulty medium`, below).

## Work and errands
- Errands resolve briskly. One person handles what several would, or the town comes to the party: a buyer seeks them out, or a message finds them at the inn.
- Work finds travellers. Once the party is known, people offer deliveries, messages and passengers along the route. Make one offer when it fits.
- A completed delivery that met a complication earns milestone XP (`--difficulty medium`).

## Hooks and clocks (refresh on every arrival and departure)
- `## Next Hooks` in `state.md`: two or three bullets, each saying who is there, what they need and how it ties to the current beat.
- `## Clocks` in `state.md`: one bullet per clock, `- <in-world date>: <what happens> (seen by: <how the party could notice>)`.
- When a clock's date comes, it happens, and the party sees it. Write both sections in the bookkeeping call, never before the narration.

## Milestone XP
- `xp.py award --campaign X --characters "A,B" --difficulty medium --type noncombat --note "..."`, then `send.py --xp-award '<json>'`, in the bookkeeping call.
