from __future__ import annotations

from typing import Dict, List
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord.commands import slash_command
import asyncio 


from config import GUILD_ID, IS_DEV
from utils.perms import is_mod
from utils.ephemeral import should_be_ephemeral
from db import individual_results


# Public categories (anything not matched falls into "Other")
PUBLIC_CATEGORIES: dict[str, list[str]] = {
    "Main": [
        "track",
        "newdeck",
    ],
    "Timer": [
        "timer",
        "endtimer",
        "pausetimer",
        "resumetimer",
    ],
    "Stats": [
        "deckstats",
        "playerstats",
        "leaderboard",
        "generalstats",
        "estousempreemultimo",
    ],
    "Info": [
        "helpcommands",
        "listdecks",
    ],
}

# Exclude some public commands from the list entirely
EXCLUDED_PUBLIC: set[str] = {
    "abegasiosinterasios",
}

# Admin commands + categories (kept from earlier)
ADMIN_COMMANDS: dict[str, str] = {
    "edittrack": "Edit a tracked match (deck/result/seat).",
    "setplayerdeck": "Set a player's deck (with autocomplete) - for /edittrack.",
    "setplayer": "Change the player in a seat (member picker) - for /edittrack.",
    "removedeckfromdatabase": "Remove a deck; optionally transfer logs/stats.",
    "findmisnameddecks": "Find deck names in logs that aren't in the DB.",
    "correctmisnameddecks": "Fix a misnamed deck across logs and stats.",
    "editdeckindatabase": "Rename a deck across DB and logs.",
    "deletetrack": "Delete a tracked match by its ID.",
    "reindex": "Ensure MongoDB indexes (mods only).",
    
}

ADMIN_CATEGORIES: dict[str, list[str]] = {
    "Deck Admin": [
        "removedeckfromdatabase",
        "findmisnameddecks",
        "correctmisnameddecks",
        "editdeckindatabase",
        "reindex"
    ],
    "Match Admin": [
        "edittrack",
        "setplayerdeck",
        "setplayer",
        "deletetrack"
    ],
}

# --- helper bucketing (put below configs) ---

def _bucket_commands(
    all_cmds: list[dict], categories: dict[str, list[str]]
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Return (by_category, leftovers) where leftovers are cmds not mapped."""
    name_map = {c["name"]: c for c in all_cmds}
    used = set()
    by_cat: dict[str, list[dict]] = {cat: [] for cat in categories}

    for cat, names in categories.items():
        for n in names:
            cmd = name_map.get(n)
            if cmd:
                by_cat[cat].append(cmd)
                used.add(n)

    leftovers = [c for c in all_cmds if c["name"] not in used]
    return by_cat, leftovers


def _fmt_cmd_list(cmds: list[dict], *, keep_order: bool = False) -> str:
    if not cmds:
        return "_none_"
    items = cmds if keep_order else sorted(cmds, key=lambda x: x["name"].lower())

    lines = []
    for c in items:
        desc = c["desc"] or ADMIN_COMMANDS.get(c["name"], "_no description_")
        if desc and not desc.endswith((".", "!", "?")):
            desc += "."
        lines.append(f"• `/{c['name']}` — {desc}")
    return "\n".join(lines)


def _embed_from_buckets(
    title: str,
    buckets: dict[str, list[dict]],
    leftovers: list[dict],
    *,
    color: int,
) -> discord.Embed:
    embed = discord.Embed(title=title, color=color)
    # Respect the order you defined in the category dicts:
    for cat, cmds in buckets.items():
        if cmds:
            embed.add_field(name=cat, value=_fmt_cmd_list(cmds, keep_order=True), inline=False)
    # Leftovers: still sort alphabetically
    if leftovers:
        embed.add_field(name="Other", value=_fmt_cmd_list(leftovers, keep_order=False), inline=False)
    return embed


# ---------- helpers for general stats ----------

POSTBAN_START_DATE = datetime(2024, 9, 24, tzinfo=timezone.utc)

async def _get_win_stats(match_criteria: dict) -> tuple[Dict[int, int], int]:
    total_per_seat: Dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    total_ir = 0

    pipeline_total = [
        {"$match": match_criteria},
        {"$group": {"_id": "$seat", "cnt": {"$sum": 1}}},
    ]
    async for row in individual_results.aggregate(pipeline_total):
        seat = int(row["_id"]) if row["_id"] in (1, 2, 3, 4) else None
        if seat:
            total_per_seat[seat] = int(row["cnt"])
            total_ir += int(row["cnt"])

    wins_by_seat: Dict[int, int] = {}
    pipeline_wins = [
        {"$match": {**match_criteria, "result": "win"}},
        {"$group": {"_id": "$seat", "wins": {"$sum": 1}}},
    ]
    async for row in individual_results.aggregate(pipeline_wins):
        seat = int(row["_id"]) if row["_id"] in (1, 2, 3, 4) else None
        if seat:
            wins_by_seat[seat] = int(row["wins"])

    win_pct: Dict[int, int] = {}
    for seat in (1, 2, 3, 4):
        total = total_per_seat.get(seat, 0)
        wins = wins_by_seat.get(seat, 0)
        pct = round((wins / total) * 100) if total > 0 else 0
        win_pct[seat] = pct

    total_games = total_ir // 4
    return win_pct, total_games


def _format_stats_field(stats_by_seat: Dict[int, int]) -> str:
    total_sum = sum(stats_by_seat.get(s, 0) for s in (1, 2, 3, 4))
    suffix = {1: "st", 2: "nd", 3: "rd"}
    lines: List[str] = [
        f"{seat}{suffix.get(seat,'th')}: {stats_by_seat.get(seat, 0)}%"
        for seat in (1, 2, 3, 4)
    ]
    draw_pct = max(0, 100 - total_sum)
    lines.append(f"**Draw**: {int(draw_pct)}%")
    return "\n".join(lines)


def _build_general_stats_embed(stats: dict) -> discord.Embed:
    preban_stats = stats["preban_stats"]
    postban_stats = stats["postban_stats"]
    preban_total = stats["preban_total_games"]
    postban_total = stats["postban_total_games"]

    embed = discord.Embed(title="General Stats", color=0xFF0000 if IS_DEV else 0x00FF00)
    embed.add_field(
        name=f"Global Win Percentage by Seat (PRE-BAN) ({preban_total} games)",
        value=_format_stats_field(preban_stats) or "_no data_",
        inline=False,
    )
    embed.add_field(
        name=f"Global Win Percentage by Seat (POST-BAN) ({postban_total} games)",
        value=_format_stats_field(postban_stats) or "_no data_",
        inline=False,
    )
    embed.set_footer(text="Cutover date: 2024-09-24 (UTC)")
    return embed


async def _fetch_general_stats() -> dict:
    preban_stats, preban_total = await _get_win_stats({"date": {"$lt": POSTBAN_START_DATE}})
    postban_stats, postban_total = await _get_win_stats({"date": {"$gte": POSTBAN_START_DATE}})
    return {
        "preban_stats": preban_stats,
        "preban_total_games": preban_total,
        "postban_stats": postban_stats,
        "postban_total_games": postban_total,
    }


# ---------- Cog ----------

class General(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @slash_command(
        guild_ids=[GUILD_ID],
        name="helpcommands",
        description="List available commands by category.",
    )
    async def helpcommands(self, ctx: discord.ApplicationContext):
        eph = should_be_ephemeral(ctx)

        # Collect registered application commands (dedupe by name)
        seen = set()
        all_cmds: list[dict] = []
        for cmd in getattr(self.bot, "application_commands", []):
            name = getattr(cmd, "name", None)
            desc = getattr(cmd, "description", "") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            all_cmds.append({"name": name, "desc": desc})

        # Split into public vs admin using your mappings
        admin_names = set(ADMIN_COMMANDS.keys()) | {n for lst in ADMIN_CATEGORIES.values() for n in lst}
        public_cmds = [c for c in all_cmds if c["name"] not in admin_names and c["name"] not in EXCLUDED_PUBLIC]
        admin_cmds  = [c for c in all_cmds if c["name"] in admin_names]

        # Bucket public commands into your categories (+ leftovers -> "Other")
        pub_buckets, pub_leftovers = _bucket_commands(public_cmds, PUBLIC_CATEGORIES)
        public_embed = _embed_from_buckets("Available Commands", pub_buckets, pub_leftovers, color=0xFF0000 if IS_DEV else 0x00FF00)

        # If caller is a mod, also prepare the admin embed
        admin_embed = None
        if is_mod(ctx.author):
            admin_buckets, admin_leftovers = _bucket_commands(admin_cmds, ADMIN_CATEGORIES)
            admin_embed = _embed_from_buckets("Admin Commands (Mods Only)", admin_buckets, admin_leftovers, color=0xFF0000 if IS_DEV else 0x00FF00)

        # 1) If everything is ephemeral, send BOTH in one message (one webhook call)
        if eph and admin_embed is not None:
            await ctx.respond(embeds=[public_embed, admin_embed], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return

        # 2) Otherwise: send the public embed first (honor eph), then pace the admin follow-up
        await ctx.respond(embed=public_embed, ephemeral=eph, allowed_mentions=discord.AllowedMentions.none())

        if admin_embed is not None:
            # tiny pause to avoid follow-up hitting the webhook rate-limit bucket
            await asyncio.sleep(0.7)
            await ctx.followup.send(embed=admin_embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @slash_command(
        guild_ids=[GUILD_ID],
        name="generalstats",
        description="Show global seat win% pre-/post-ban (cutover: 2024-09-24 UTC).",
    )
    async def generalstats(self, ctx: discord.ApplicationContext):
        eph = should_be_ephemeral(ctx)
        await ctx.defer(ephemeral=eph)

        stats = await _fetch_general_stats()
        embed = _build_general_stats_embed(stats)
        await ctx.followup.send(embed=embed, ephemeral=eph)


def setup(bot: discord.Bot):
    bot.add_cog(General(bot))
