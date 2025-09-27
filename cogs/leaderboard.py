from typing import Annotated

import discord
from discord.ext import commands
from discord.commands import slash_command, Option

from config import GUILD_ID, IS_DEV
from db import individual_results
from utils.time_ranges import get_period_start, previous_month_window, format_period
from utils.ephemeral import should_be_ephemeral


class Leaderboard(commands.Cog):
    def __init__(self, bot): 
        self.bot = bot

    @slash_command(guild_ids=[GUILD_ID], name="leaderboard", description="Top players or decks")
    async def leaderboard(
        self,
        ctx: discord.ApplicationContext,
        type: Annotated[str, Option(str, "Type", choices=["players", "decks"])],
        period: Annotated[str, Option(str, "Period", choices=["1m", "3m", "6m", "1y", "all"], default="3m")],
        postban: Annotated[bool, Option(bool, "Use post-ban date?", default=True)],
    ):
        await (self.show_players(ctx, period, postban) if type == "players" else self.show_decks(ctx, period, postban))

    async def show_players(self, ctx, period, postban):
        start = get_period_start(period, postban)
        readable = format_period(period)
        title_suffix = " (POST-BAN)" if postban else ""
        result_limit = 40 if period != "1m" else None
        prev_start, prev_end = previous_month_window(period)
        await ctx.defer(ephemeral=should_be_ephemeral(ctx))

        pipeline = [
            {"$match": {"date": {"$gte": start}}},
            {"$group": {"_id": "$player_id",
                        "games_played": {"$sum": 1},
                        "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                        "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                        "draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}}}},
            {"$addFields": {
                "weighted_wins": {"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]},
                "normal_win_percentage": {"$multiply": [{"$divide": ["$wins", "$games_played"]}, 100]},
                "weighted_win_percentage": {"$multiply": [{"$divide": [{"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]}, "$games_played"]}, 100]}
            }},
            {"$match": {"games_played": {"$gte": 15}}},
            {"$sort": {"weighted_win_percentage": -1, "games_played": -1}},
        ]
        if result_limit:
            pipeline.append({"$limit": result_limit})

        guild = ctx.guild
        embeds, embed, pos, fields = [], discord.Embed(title=f"Player Leaderboard - {readable}{title_suffix}", color=0xFF0000 if IS_DEV else 0x00FF00), 1, 0
        current = {}
        async for d in individual_results.aggregate(pipeline):
            try:
                m = guild.get_member(d['_id'])
                if not m:
                    try:
                        m = await guild.fetch_member(d['_id'])
                    except discord.NotFound:
                        continue
            except discord.NotFound:
                continue
            medal = "🥇" if pos == 1 else "🥈" if pos == 2 else "🥉" if pos == 3 else f"{pos}."
            embed.add_field(
                name=f"{medal} **{(m.nick or m.display_name)}**: {d['wins']}W | {d['losses']}L | {d['draws']}D",
                value=f"• Win: **{int(d['normal_win_percentage'])}**% | *🏋Win%: **{int(d['weighted_win_percentage'])}**%* | (Games: {d['games_played']}) | ID: {d['_id']}",
                inline=False
            )
            current[d['_id']] = d['weighted_win_percentage']
            pos += 1; fields += 1
            if fields >= 25:
                embeds.append(embed)
                embed = discord.Embed(title=f"Player Leaderboard - {readable}{title_suffix}", color=0xFF0000 if IS_DEV else 0x00FF00)
                fields = 0
        if fields:
            embeds.append(embed)

        if period == "1m" and prev_start and prev_end:
            prev_pipe = [
                {"$match": {"date": {"$gte": prev_start, "$lt": prev_end}}},
                {"$group": {"_id": "$player_id",
                            "games_played": {"$sum": 1},
                            "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                            "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                            "draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}}}},
                {"$addFields": {
                    "weighted_wins": {"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]},
                    "weighted_win_percentage": {"$multiply": [{"$divide": [{"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]}, "$games_played"]}, 100]}
                }},
            ]
            async for d in individual_results.aggregate(prev_pipe):
                cur = current.get(d['_id'])
                if cur is None:
                    continue
                prev = d['weighted_win_percentage']; delta = cur - prev
                mark = '🔄 0%' if -1 < delta < 1 else (f"{'⬆️' if delta>0 else '🔻'} {abs(int(delta))}%")
                for em in embeds:
                    for i, field in enumerate(em.fields):
                        if f"ID: {d['_id']}" in field.value:
                            em.set_field_at(
                                i,
                                name=field.name,
                                value=field.value.replace(f"**{int(cur)}**%*", f"**{int(cur)}**%* ({mark})"),
                                inline=field.inline,
                            )

        # strip IDs
        for em in embeds:
            for i, field in enumerate(em.fields):
                em.set_field_at(i, name=field.name, value=field.value.split(" | ID:")[0], inline=field.inline)
        if not embeds:
            embeds = [discord.Embed(title=f"Player Leaderboard - {readable}{title_suffix}", description="No results found.", color=0xFF0000 if IS_DEV else 0x00FF00)]
        for i, e in enumerate(embeds):
            if i:
                e.title = None
        await ctx.respond(embeds=embeds, ephemeral=should_be_ephemeral(ctx))

    async def show_decks(self, ctx, period, postban):
        start = get_period_start(period, postban)
        readable = format_period(period)
        title_suffix = " (POST-BAN)" if postban else ""
        result_limit = 40 if period != "1m" else None
        prev_start, prev_end = previous_month_window(period)
        await ctx.defer(ephemeral=should_be_ephemeral(ctx))

        pipeline = [
            {"$match": {"date": {"$gte": start}}},
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
            {"$match": {"games_played": {"$gte": 15}}},
            {"$sort": {"weighted_win_percentage": -1, "games_played": -1}},
        ]
        if result_limit:
            pipeline.append({"$limit": result_limit})

        embeds, embed, pos, fields = [], discord.Embed(title=f"Decks Leaderboard - {readable}{title_suffix}", color=0xFF0000 if IS_DEV else 0x00FF00), 1, 0
        current = {}
        async for d in individual_results.aggregate(pipeline):
            name = d['_id']
            medal = "🥇" if pos == 1 else "🥈" if pos == 2 else "🥉" if pos == 3 else f"{pos}."
            embed.add_field(
                name=f"{medal} {name}: {d['wins']}W | {d['losses']}L | {d['draws']}D",
                value=f"• W: **{int(d['normal_win_percentage'])}**% | *🏋W: **{int(d['weighted_win_percentage'])}**%* | (Games: {d['games_played']}) | ID: {name}",
                inline=False
            )
            current[name] = d['weighted_win_percentage']
            pos += 1; fields += 1
            if fields >= 25:
                embeds.append(embed)
                embed = discord.Embed(title=f"Decks Leaderboard - {readable}{title_suffix}", color=0xFF0000 if IS_DEV else 0x00FF00)
                fields = 0
        if fields:
            embeds.append(embed)

        if period == "1m" and prev_start and prev_end:
            prev_pipe = [
                {"$match": {"date": {"$gte": prev_start, "$lt": prev_end}}},
                {"$group": {"_id": "$deck_name",
                            "games_played": {"$sum": 1},
                            "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                            "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                            "draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}}}},
                {"$addFields": {
                    "weighted_wins": {"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]},
                    "weighted_win_percentage": {"$multiply": [{"$divide": [{"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]}, "$games_played"]}, 100]}
                }},
            ]
            async for d in individual_results.aggregate(prev_pipe):
                cur = current.get(d['_id'])
                if cur is None:
                    continue
                prev = d['weighted_win_percentage']; delta = cur - prev
                mark = '🔄 0%' if -1 < delta < 1 else (f"{'⬆️' if delta>0 else '🔻'} {abs(int(delta))}%")
                for em in embeds:
                    for i, field in enumerate(em.fields):
                        if f"ID: {d['_id']}" in field.value:
                            em.set_field_at(
                                i,
                                name=field.name,
                                value=field.value.replace(f"**{int(cur)}**%*", f"**{int(cur)}**%* ({mark})"),
                                inline=field.inline,
                            )

        # strip IDs
        for em in embeds:
            for i, field in enumerate(em.fields):
                em.set_field_at(i, name=field.name, value=field.value.split(" | ID:")[0], inline=field.inline)
        if not embeds:
            embeds = [discord.Embed(title=f"Decks Leaderboard - {readable}{title_suffix}", description="No results found.", color=0xFF0000 if IS_DEV else 0x00FF00)]
        for i, e in enumerate(embeds):
            if i:
                e.title = None
        await ctx.respond(embeds=embeds, ephemeral=should_be_ephemeral(ctx))


def setup(bot):
    bot.add_cog(Leaderboard(bot))
