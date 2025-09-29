# CA Match Logger (Discord Bot) 🃏📊 

Track **Magic: The Gathering** 4-player games in a single guild, keep per-deck and per-player stats, and run quick admin tools — all with slash commands. 

> Built with **discord.py**, **MongoDB (Motor)**, and a modular **cogs** layout.

---

## ✨ Features

- /track — log a 4-player match (auto IDs, W/L/D attribution)
- /events — view the next scheduled event & **register/unregister** 
- Timer integration (stops active voice timers after /track, if present)

Admin deck tools: 
- /edittrack — interactive editor: change seat, deck, result, or player
- /deletetrack — **confirm/cancel** delete, auto-fixes affected deck stats
- /removedeckfromdatabase (with optional transfer) 
- /editdeckindatabase (rename everywhere) 
- /findmisnameddecks + /correctmisnameddecks