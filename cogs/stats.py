from typing import Annotated

import discord
from discord.ext import commands
from discord.commands import slash_command, Option

from config import GUILD_ID, IS_DEV
from db import individual_results, decks
from utils.time_ranges import get_period_start, previous_month_window, format_period
from utils.text import capitalize_words, paginate_text
from utils.views import PaginatorView
from utils.ephemeral import should_be_ephemeral



class Stats(commands.Cog):
    def __init__(self, bot): 
        self.bot = bot
        
    async def deck_autocomplete(ctx: discord.AutocompleteContext):
        cursor = decks.find({}, {"name": 1, "_id": 0})
        all_decks = [doc["name"] async for doc in cursor]
        q = (ctx.value or "").lower()
        return [d for d in all_decks if q in d.lower()]

    
    async def fetch_deck_stats(self, deck_name: str, period: str, postban: bool):
        start = get_period_start(period, postban)

        totals_pipe = [
            {
                "$match": {
                    "deck_name": {"$regex": f"^{deck_name}$", "$options": "i"},
                    "date": {"$gte": start},
                }
            },
            {
                "$group": {
                    "_id": None,
                    "wins":     {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                    "losses":   {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                    "draws":    {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}},
                    "seat1":    {"$sum": {"$cond": [{"$eq": ["$seat", 1]}, 1, 0]}},
                    "seat2":    {"$sum": {"$cond": [{"$eq": ["$seat", 2]}, 1, 0]}},
                    "seat3":    {"$sum": {"$cond": [{"$eq": ["$seat", 3]}, 1, 0]}},
                    "seat4":    {"$sum": {"$cond": [{"$eq": ["$seat", 4]}, 1, 0]}},
                    "winseat1": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 1]}, {"$eq": ["$result", "win"]}]} , 1, 0]}},
                    "winseat2": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 2]}, {"$eq": ["$result", "win"]}]} , 1, 0]}},
                    "winseat3": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 3]}, {"$eq": ["$result", "win"]}]} , 1, 0]}},
                    "winseat4": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 4]}, {"$eq": ["$result", "win"]}]} , 1, 0]}},
                }
            },
        ]
        total_doc = await individual_results.aggregate(totals_pipe).to_list(length=1)
        if not total_doc:
            return None
        totals = total_doc[0]

        top_pipe = [
            {
                "$match": {
                    "deck_name": {"$regex": f"^{deck_name}$", "$options": "i"},
                    "date": {"$gte": start},
                }
            },
            {
                "$group": {
                    "_id": "$player_id",
                    "games_played": {"$sum": 1},
                    "wins":   {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                    "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                    "draws":  {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}},
                }
            },
            {
                "$addFields": {
                    "weighted_wins": {"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]},
                    "win_percentage": {
                        "$multiply": [{"$divide": ["$wins", "$games_played"]}, 100]
                    },
                    "weighted_win_percentage": {
                        "$multiply": [
                            {
                                "$divide": [
                                    {"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]},
                                    "$games_played",
                                ]
                            },
                            100,
                        ]
                    },
                }
            },
            {"$match": {"games_played": {"$gte": 5}}},
            {"$sort": {"weighted_win_percentage": -1, "games_played": -1}},
            {"$limit": 10},
        ]
        top_players = [d async for d in individual_results.aggregate(top_pipe)]

        return {"totals": totals, "top_players": top_players}

    

    async def fetch_player_stats(self, player_id: int, period: str, postban: bool, deck_filter: str | None):
        start_date = get_period_start(period, postban)
        match_stage = {"player_id": player_id, "date": {"$gte": start_date}}
        if deck_filter:
            match_stage["deck_name"] = {"$regex": f"^{deck_filter}$", "$options": "i"}

        top_decks = []
        if not deck_filter:
            pipeline = [
                {"$match": match_stage},
                {"$group": {"_id": "$deck_name",
                            "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                            "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                            "draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}},
                            "games_played": {"$sum": 1}}},
                {"$addFields": {
                    "weighted_wins": {"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]},
                    "normal_win_percentage": {"$multiply": [{"$divide": ["$wins", "$games_played"]}, 100]},
                    "weighted_win_percentage": {"$multiply": [{"$divide": [{"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]}, "$games_played"]}, 100]}
                }},
                {"$sort": {"weighted_win_percentage": -1}},
                {"$limit": 10},
            ]
            async for d in individual_results.aggregate(pipeline):
                top_decks.append({
                    "deck_name": capitalize_words(d['_id']),
                    "wins": d['wins'],
                    "losses": d['losses'],
                    "draws": d['draws'],
                    "games_played": d['games_played'],
                    "win_percentage": d['normal_win_percentage'],
                    "weighted_win_percentage": d['weighted_win_percentage'],
                })

        seat_pipeline = [
            {"$match": match_stage},
            {"$group": {"_id": None,
                        "total_wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                        "total_losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                        "total_draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}},
                        "seat1": {"$sum": {"$cond": [{"$eq": ["$seat", 1]}, 1, 0]}},
                        "seat2": {"$sum": {"$cond": [{"$eq": ["$seat", 2]}, 1, 0]}},
                        "seat3": {"$sum": {"$cond": [{"$eq": ["$seat", 3]}, 1, 0]}},
                        "seat4": {"$sum": {"$cond": [{"$eq": ["$seat", 4]}, 1, 0]}},
                        "winseat1": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 1]}, {"$eq": ["$result", "win"]}]}, 1, 0]}},
                        "winseat2": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 2]}, {"$eq": ["$result", "win"]}]}, 1, 0]}},
                        "winseat3": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 3]}, {"$eq": ["$result", "win"]}]}, 1, 0]}},
                        "winseat4": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 4]}, {"$eq": ["$result", "win"]}]}, 1, 0]}}}}]
        agg = await individual_results.aggregate(seat_pipeline).to_list(length=1)
        if not agg:
            return None

        out = {**agg[0],
               "wins": agg[0]['total_wins'], "losses": agg[0]['total_losses'], "draws": agg[0]['total_draws'],
               "top_10_decks": top_decks}

        if deck_filter:
            games = await individual_results.aggregate([
                {"$match": {**match_stage, "match_id": {"$ne": None}}},
                {"$lookup": {"from": "matches", "localField": "match_id", "foreignField": "match_id", "as": "game_data"}},
                {"$unwind": "$game_data"},
                {"$project": {"match_id": 1, "players": "$game_data.players", "date": "$game_data.date"}},
            ]).to_list(None)
            out["games"] = [{
                "id": g['match_id'],
                "date": g['date'],
                "players": [{"deck_name": capitalize_words(p.get("deck_name", "Unknown")),
                             "winner": p.get("result") == "win"} for p in g['players']]
            } for g in games]
        return out


    @slash_command(guild_ids=[GUILD_ID], name="playerstats", description="Get stats for a player")
    async def playerstats(
        self,
        ctx: discord.ApplicationContext,
        player: Annotated[discord.Member, Option(discord.Member, "Player")],
        period: Annotated[str, Option(str, "Period", choices=["1m", "3m", "6m", "1y", "all"], default="all")],
        postban: Annotated[bool, Option(bool, "Use post-ban date?", default=True)],
        individual_deck: Annotated[str | None, Option(str, "Filter by deck", required=False)] = None,
    ):
        stats = await self.fetch_player_stats(player.id, period, postban, individual_deck)
        rp = format_period(period); suffix = " (POST-BAN)" if postban else ""
        fmt_name = capitalize_words(individual_deck) if individual_deck else ""
        if not stats:
            msg = f"No stats found for {player.display_name}" + (f" with `{fmt_name}`." if individual_deck else ".")
            await ctx.respond(msg, ephemeral=False); 
            return

        total = stats['wins'] + stats['losses'] + stats['draws']
        winp = (stats['wins']/total*100) if total else 0
        wwinp = ((stats['wins'] + stats['draws']*0.143)/total*100) if total else 0
        wseat = lambda s: (stats[f'winseat{s}']/stats[f'seat{s}']*100) if stats[f'seat{s}'] else 0

        desc = (
            f"**Total Games**: {total}\n"
            f"**{stats['wins']}** W | **{stats['losses']}** L | **{stats['draws']}** D\n\n"
            f"**Win %**: {winp:.2f}%\n"
            f"**🏋Win %**: {wwinp:.2f}%\n\n"
            f"**Seating %**: "
            f"{(stats['seat1']/total*100 if total else 0):.0f}% (**{stats['seat1']}**) | "
            f"{(stats['seat2']/total*100 if total else 0):.0f}% (**{stats['seat2']}**) | "
            f"{(stats['seat3']/total*100 if total else 0):.0f}% (**{stats['seat3']}**) | "
            f"{(stats['seat4']/total*100 if total else 0):.0f}% (**{stats['seat4']}**)\n"
            f"**Win by Seat %**: {wseat(1):.2f}% | {wseat(2):.2f}% | {wseat(3):.2f}% | {wseat(4):.2f}%"
        )

        title = (f"Player Stats for {player.display_name} with {fmt_name} - {rp}{suffix}"
                 if individual_deck else f"Player Stats for {player.display_name} - {rp}{suffix}")
        embed = discord.Embed(title=title, description=desc, color=0xFF0000 if IS_DEV else 0x00FF00)

        if individual_deck and 'games' in stats:
            games = stats['games']; n = len(games)
            if n == 0:
                embed.description += f"\n\n🗃 0 games found with deck '{fmt_name}'."
                await ctx.respond(embed=embed); 
                return

            if n <= 5:
                dump = "\n**📜 Game Details (≤5 games):**\n"
                for g in games:
                    date = f"{g['date'].strftime('%b')} {g['date'].day}, {g['date'].year}"
                    dump += f"**Game ID**: `{g['id']}` - `{date}`\n"
                    for i, p in enumerate(g['players'], start=1):
                        name = p['deck_name']; star = "🏆" if p['winner'] else ""
                        bold = f"**{name}**" if name.lower() == fmt_name.lower() else name
                        dump += f"Seat {i}: {bold} {star}\n"
                    dump += "\n"
                embed.description += "\n" + dump
                await ctx.respond(embed=embed, ephemeral=False); 
                return

            entries = []
            for g in games:
                d = f"{g['date'].strftime('%b')} {g['date'].day}, {g['date'].year}"
                t = f"**Game ID**: `{g['id']}` - `{d}`\n"
                for i, p in enumerate(g['players'], start=1):
                    name = p['deck_name']; star = "🏆" if p['winner'] else ""
                    t += f"Seat {i}: {name} {star}\n"
                entries.append(t.strip())
            pages = paginate_text(entries)
            view = PaginatorView(author=ctx.author, pages=pages)
            first = discord.Embed(title=f"📜 Game Dump (Page 1/{len(pages)})", description=pages[0], color=0x00FFCC)
            await ctx.respond(embed=first, view=view, ephemeral=True); 
            return

        await ctx.respond(embed=embed, ephemeral=should_be_ephemeral(ctx))
        
        
    @slash_command(guild_ids=[GUILD_ID], name="deckstats", description="Get statistics for a deck.")
    async def deckstats(
        self,
        ctx: discord.ApplicationContext,
        deck: Annotated[str, Option(str, "Select a deck", autocomplete=deck_autocomplete)],
        period: Annotated[str, Option(str, "Period", choices=["1m", "3m", "6m", "1y", "all"], default="all")],
        postban: Annotated[bool, Option(bool, "Use post-ban date?", default=True)],
    ):
        await ctx.defer(ephemeral=should_be_ephemeral(ctx))

        stats = await self.fetch_deck_stats(deck, period, postban)
        readable = format_period(period)
        title_suffix = " (POST-BAN)" if postban else ""

        if not stats:
            await ctx.followup.send(f"No stats found for deck {capitalize_words(deck)}.", ephemeral=should_be_ephemeral(ctx))
            return

        t = stats["totals"]
        total_games = t["wins"] + t["losses"] + t["draws"]
        win_percentage = (t["wins"] / total_games * 100) if total_games else 0.0
        weighted_win_percentage = ((t["wins"] + t["draws"] * 0.143) / total_games * 100) if total_games else 0.0

        # Seat win%
        def seat_win_pct(seat):
            s = t.get(f"seat{seat}", 0) or 0
            w = t.get(f"winseat{seat}", 0) or 0
            return (w / s * 100) if s else 0.0

        deckStatsMessage = (
            f"**Total Games Played:** {total_games}\n"
            f"**{t['wins']}** W | **{t['losses']}** L | **{t['draws']}** D\n\n"
            f"**Win %:** {win_percentage:.2f}%\n"
            f"**🏋Win %**: {weighted_win_percentage:.2f}%\n\n"
            f"**Seating %**: "
            f"{((t['seat1']/total_games)*100 if total_games else 0):.0f}% (**{t['seat1']}**) | "
            f"{((t['seat2']/total_games)*100 if total_games else 0):.0f}% (**{t['seat2']}**) | "
            f"{((t['seat3']/total_games)*100 if total_games else 0):.0f}% (**{t['seat3']}**) | "
            f"{((t['seat4']/total_games)*100 if total_games else 0):.0f}% (**{t['seat4']}**)\n"
            f"**Win by Seat %**: {seat_win_pct(1):.2f}% | {seat_win_pct(2):.2f}% | {seat_win_pct(3):.2f}% | {seat_win_pct(4):.2f}%\n\n"
            "**Top 10 Players:**\n"
        )

        # Resolve member names efficiently
        guild = ctx.guild
        lines = []
        for d in stats["top_players"]:
            pid = d["_id"]
            m = guild.get_member(pid)
            if not m:
                try:
                    m = await guild.fetch_member(pid)
                except discord.NotFound:
                    continue
            display = (m.nick or m.display_name)
            lines.append(
                f"- **{display}** - {d['wins']} W | {d['losses']} L | {d['draws']} D - "
                f"Games Played: {d['games_played']}, Win%: {int(d['win_percentage'])}%, 🏋Win%: {int(d['weighted_win_percentage'])}%"
            )

        deckStatsMessage += ("\n".join(lines) if lines else "_No players with ≥5 games._")

        embed = discord.Embed(
            title=f"Deck Stats for {capitalize_words(deck)} - {readable}{title_suffix}",
            description=deckStatsMessage,
            color=0xFF0000 if IS_DEV else 0x00FF00
        )
        await ctx.followup.send(embed=embed, ephemeral=should_be_ephemeral(ctx))

        
        
    @slash_command(guild_ids=[GUILD_ID], name="estousempreemultimo", description="Show the last 10 seatings of a player.")
    async def estousempreemultimo(self, ctx: discord.ApplicationContext, player: discord.Member):
        """Show the last 10 seatings of the given player."""

        # Fetch last 10 games where the player participated
        cursor = individual_results.find(
            {"player_id": player.id},
            {"seat": 1, "date": 1, "match_id": 1, "_id": 0}
        ).sort("date", -1).limit(10)

        last_10_games = await cursor.to_list(length=10)

        if not last_10_games:
            await ctx.respond(f"{player.display_name} doesn't have 10 games.")
            return

        # Build seating info and seat counters
        seating_info = ""
        seat1 = seat2 = seat3 = seat4 = 0

        for game in last_10_games:
            seating_info += f"**Game {game['match_id']}** - Seat: {game['seat']}\n"
            if game["seat"] == 1:
                seat1 += 1
            elif game["seat"] == 2:
                seat2 += 1
            elif game["seat"] == 3:
                seat3 += 1
            elif game["seat"] == 4:
                seat4 += 1

        seat_summary = f"**Summary:** **{seat1}** | **{seat2}** | **{seat3}** | **{seat4}**"

        embed = discord.Embed(
            title=f"Last 10 Seatings for {player.display_name}",
            description=f"{seat_summary}\n\n{seating_info}",
            color=0xFF0000 if IS_DEV else 0x00FF00,
        )

        await ctx.respond(embed=embed, ephemeral=should_be_ephemeral(ctx))


def setup(bot):
    bot.add_cog(Stats(bot))
