# Trade

Read once when the party buys, sells or haggles.

- The players say yes to every deal. A trader's offer, a buyer's bid, a fee or a bribe demanded: give the figure and what hangs on it, then stop. Move coin or goods only for a deal a player agreed to, in their input or in a standing instruction that names it. If they only asked the price, the price is the answer.
- A bribe is theirs to offer and to size. When an NPC wants one, or the party offers one without a sum, the NPC names what it takes and the party decides.
- A trade the players declared ("we buy the lantern") fits in one message: the offer, what inspection shows, the DC and stake if anyone haggles, the roll, the result and the new purse.
- Once they choose to trade, apply the `Trade policy:` line in `## Pinned Facts` as their standing orders. It says the margin they aim for, whether to inspect and who haggles, and covers nothing it doesn't name.
- Slow down only when the deal carries the current beat or a relationship.
- Haggling DC by the trader's stance: 10 for one who needs the deal, 13 ordinary, 16 for one who won't budge, 19 hostile. The price moves 10% per band: success is 10% their way, beating the DC by 5 is 20%, failure holds the price, failing by 5 costs goodwill or the deal for now.
- Roll Perception on goods only when there is something to find. Otherwise say the goods are sound.
- Every coin and item change goes through `inventory.py`, before the narration call, so the narration quotes its one-line result:
  - `inventory.py buy|sell --campaign X --character NAME --price "2 gp" [--qty N] ITEM`
  - `inventory.py spend|earn --campaign X --character NAME "2 gp 5 sp"`
  - `inventory.py add|remove --campaign X --character NAME [--qty N] ITEM`; `cargo add|remove|show --campaign X [ITEM]`
  - It does the sums and change, refuses what the purse cannot cover, and rewrites the sheet and the display. Never edit `## Equipment & Inventory` by hand.
- Sums and ledgers stay out of the spoken fiction. Put them in the narration call's `@tutor` block, one line.
- A trader is an NPC: open on what they are doing or worried about, and give them one want (npcs.md).
