# NPCs

Read once when a named NPC first matters this session.

- Skip the name-and-price greeting. Open on what the person is doing or worried about.
- The main NPC in each scene wants one thing from the party. When their want is marked as an ask, they put it to the party.
- A person who is asked gives a usable answer, with a price, a name or a condition. Don't pass the party from person to person.
- For a new named NPC in an improvised campaign, run `oracle.py npc --campaign X --name "Full Name"` before the narration call (add `--trader` for anyone buying or selling). It saves them to `npcs.md`, and the same name always gives the same person. Play their one trait lightly, every time they appear.
- The first time a new NPC speaks, give them a voice on their block: `@npc Full Name voice=<id>`, chosen for gender, age and manner from `bm_george`, `bm_lewis`, `am_fenrir`, `am_onyx` (men), `af_heart`, `af_bella`, `bf_alice`, `bf_emma`, `af_sky` (women); `b` is British, `a` American. Not the narrator's voice, and not one another person in the scene already has. A voice picked in the menu is kept.
- Before voicing an NPC who matters, read their entry once (`npcs.md`, and their section of `npcs-full.md` if it exists). The turn card's "People here" line is enough for everyone else.
- After a scene with an NPC, in the bookkeeping call: `oracle.py npc note --campaign X --name "Full Name" "<one line>"`, for example `"Sold 4 blankets at 18. Thinks they drive a hard bargain."`
