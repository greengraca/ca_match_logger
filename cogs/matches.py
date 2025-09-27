from typing import Annotated
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord.commands import slash_command, Option

from config import GUILD_ID, IS_DEV
from db import matches, individual_results, decks as decks_col, counters as counters_col
from utils.text import capitalize_words


# Top-level autocomplete to be used inside annotations
async def deck_autocomplete(ctx: discord.AutocompleteContext):
    cursor = decks_col.find({}, {"name": 1, "_id": 0})
    names = [d["name"] async for d in cursor]
    needle = (ctx.value or "").lower()
    return [n for n in names if needle in n.lower()][:25]


class Matches(commands.Cog):
    def __init__(self, bot): 
        self.bot = bot

    async def get_next_match_id(self) -> int:
        await counters_col.find_one_and_update({"_id": "match_id"}, {"$inc": {"sequence_value": 1}}, upsert=True)
        doc = await counters_col.find_one({"_id": "match_id"})
        return doc["sequence_value"] if doc else 1

    async def deck_exists(self, name: str) -> bool:
        return await decks_col.find_one({"name": name}) is not None

    async def insert_match_result(self, match_details: dict):
        await matches.insert_one(match_details)
        # denormalized individual results
        for p in match_details["players"]:
            await individual_results.insert_one(
                {
                    "player_id": p["player_id"],
                    "deck_name": p["deck_name"],
                    "seat": p["position"],
                    "result": p["result"],
                    "match_id": match_details["match_id"],
                    "date": match_details["date"],
                }
            )
        # ensure players exist in deck doc and update W/L/D
        for p in match_details["players"]:
            exists = await decks_col.find_one({"name": p["deck_name"], "players.player_id": p["player_id"]})
            if not exists:
                await decks_col.update_one(
                    {"name": p["deck_name"]},
                    {"$addToSet": {"players": {"player_id": p["player_id"], "wins": 0, "losses": 0, "draws": 0}}},
                )
            field = {"win": "players.$.wins", "loss": "players.$.losses", "draw": "players.$.draws"}.get(p["result"])
            if field:
                await decks_col.update_one({"name": p["deck_name"], "players.player_id": p["player_id"]}, {"$inc": {field: 1}})

    @slash_command(guild_ids=[GUILD_ID], name="track", description="Track a 4-player match.")
    async def track(
        self,
        ctx: discord.ApplicationContext,
        player1: Annotated[discord.Member, Option(discord.Member, "Player 1")],
        deck1: Annotated[str, Option(str, "Deck 1", autocomplete=deck_autocomplete)],
        player2: Annotated[discord.Member, Option(discord.Member, "Player 2")],
        deck2: Annotated[str, Option(str, "Deck 2", autocomplete=deck_autocomplete)],
        player3: Annotated[discord.Member, Option(discord.Member, "Player 3")],
        deck3: Annotated[str, Option(str, "Deck 3", autocomplete=deck_autocomplete)],
        player4: Annotated[discord.Member, Option(discord.Member, "Player 4")],
        deck4: Annotated[str, Option(str, "Deck 4", autocomplete=deck_autocomplete)],
        winner: Annotated[str, Option(str, "Winner", choices=["Player 1", "Player 2", "Player 3", "Player 4", "Draw"])],
    ):
        # validate decks
        missing = []
        for d in [deck1, deck2, deck3, deck4]:
            if not await self.deck_exists(d):
                missing.append(d)
        if missing:
            lst = ", ".join(f"`{m}`" for m in missing)
            await ctx.respond(f"The following decks do not exist: {lst}", ephemeral=True)
            return

        match_id = await self.get_next_match_id()

        def res(i: int) -> str:
            if winner == f"Player {i}":
                return "win"
            if winner == "Draw":
                return "draw"
            return "loss"

        md = {
            "match_id": match_id,
            "players": [
                {"player_id": player1.id, "deck_name": deck1, "position": 1, "result": res(1)},
                {"player_id": player2.id, "deck_name": deck2, "position": 2, "result": res(2)},
                {"player_id": player3.id, "deck_name": deck3, "position": 3, "result": res(3)},
                {"player_id": player4.id, "deck_name": deck4, "position": 4, "result": res(4)},
            ],
            "date": datetime.now(timezone.utc),
        }
        await self.insert_match_result(md)

        d1, d2, d3, d4 = map(capitalize_words, [deck1, deck2, deck3, deck4])
        mapping = {"Player 1": player1, "Player 2": player2, "Player 3": player3, "Player 4": player4}
        desc = (
            f"A game has been logged.\n\nGame ID: {match_id}\n\n"
            f"Player 1: {player1.mention}, playing {d1}\n"
            f"Player 2: {player2.mention}, playing {d2}\n"
            f"Player 3: {player3.mention}, playing {d3}\n"
            f"Player 4: {player4.mention}, playing {d4}\n\n"
            f"{'The winner was ' + mapping[winner].mention if winner!='Draw' else 'The game was a draw.'}"
        )
        await ctx.respond(embed=discord.Embed(title="Game Log", description=desc, color=0xFF0000 if IS_DEV else 0x00FF00))

        # TimerCog integration (if present)
        timer_cog = self.bot.get_cog("TimerCog")
        if timer_cog and (vs := ctx.author.voice) and vs.channel:
            vc_id = vs.channel.id
            if vc_id in getattr(timer_cog, "voice_channel_timers", {}):
                seq = timer_cog.voice_channel_timers[vc_id]
                timer_id = f"{vc_id}_{seq}"
                if timer_cog.is_user_in_timer(str(ctx.author.id), timer_id):
                    await timer_cog.set_timer_stopped(timer_id)


def setup(bot):
    bot.add_cog(Matches(bot))
