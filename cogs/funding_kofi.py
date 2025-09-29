# cogs/funding_kofi.py
from __future__ import annotations
import os, re, math, aiohttp, asyncio
from datetime import datetime, timezone
from typing import Annotated, Optional, Dict, Any

import discord
from discord.ext import commands, tasks
from discord.commands import slash_command, Option

from config import GUILD_ID, IS_DEV
from db import db  # expose Motor client/db or import specific collections if you prefer

# Collections
funding_months   = db.funding_months      # one doc per (guild_id, month)
funding_pool     = db.funding_pool        # one doc per guild: prize_pool_cents
funding_tokens   = db.funding_tokens      # token -> {guild_id, user_id, created}
funding_meta     = db.funding_meta        # cursors, e.g. last seen Ko-fi txn ids

OWNER_ID = 399635760254550026
ROLE_NAME = "Arena Vanguard"  # change if you want

def month_key(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"

def eur_to_cents(x: float) -> int:
    return max(0, int(round(x * 100)))

def cents_to_eur(c: int) -> str:
    return f"{c/100:.2f}"

def make_bar(progress: float, width: int = 24) -> str:
    # progress: 0..1
    filled = max(0, min(width, int(round(progress * width))))
    return "▰" * filled + "▱" * (width - filled)

def default_goal_cents() -> int:
    return eur_to_cents(10.0)

async def ensure_role(guild: discord.Guild) -> Optional[discord.Role]:
    role = discord.utils.get(guild.roles, name=ROLE_NAME)
    if role:
        return role
    try:
        return await guild.create_role(name=ROLE_NAME, reason="Supporter role for Ko-fi donors")
    except Exception:
        return None

async def give_role(guild: discord.Guild, user_id: int):
    role = await ensure_role(guild)
    if not role:
        return
    member = guild.get_member(user_id) or await guild.fetch_member(user_id)
    try:
        await member.add_roles(role, reason="Ko-fi supporter")
    except Exception:
        pass

async def get_or_create_month(guild_id: int) -> Dict[str, Any]:
    mk = month_key()
    doc = await funding_months.find_one({"guild_id": guild_id, "month": mk})
    if doc:
        return doc
    # carry over prize pool (global)
    pool = await funding_pool.find_one({"guild_id": guild_id}) or {"guild_id": guild_id, "prize_pool_cents": 0}
    doc = {
        "guild_id": guild_id,
        "month": mk,
        "goal_cents": default_goal_cents(),
        "total_cents": 0,
        "channel_id": None,
        "sticky_message_id": None,
        "recurring_url": None,
        "onetime_url": None,
        "seen_txn_ids": [],  # Ko-fi txn ids processed this month
    }
    await funding_months.insert_one(doc)
    # ensure pool doc exists
    await funding_pool.update_one({"guild_id": guild_id}, {"$setOnInsert": pool}, upsert=True)
    return await funding_months.find_one({"guild_id": guild_id, "month": mk})

def make_embed(doc: Dict[str, Any], guild: discord.Guild, pool_cents: int) -> discord.Embed:
    goal = doc.get("goal_cents", default_goal_cents())
    total = doc.get("total_cents", 0)
    pct = 0.0 if goal <= 0 else min(1.0, total / goal)
    bar = make_bar(pct)
    mk = doc.get("month")
    title = f"Commander Arena — Monthly Goal ({mk})"
    desc = (
        f"**This month:** €{cents_to_eur(total)} / €{cents_to_eur(goal)}\n"
        f"{bar}  `{int(pct*100)}%`\n\n"
        f"**Prize Pool:** €{cents_to_eur(pool_cents)}"
    )
    color = 0xFF0000 if IS_DEV else 0x00FF00
    return discord.Embed(title=title, description=desc, color=color)

class FundingView(discord.ui.View):
    def __init__(self, recurring_url: Optional[str], onetime_url: Optional[str]):
        super().__init__(timeout=None)
        # Put recurring first & primary
        if recurring_url:
            self.add_item(discord.ui.Button(label="❤️ Help each month", style=discord.ButtonStyle.primary, url=recurring_url))
        if onetime_url:
            self.add_item(discord.ui.Button(label="☕ One-time help", style=discord.ButtonStyle.secondary, url=onetime_url))
        # Optional: add a “Get my code” button via link to /fund mycode instructions? (Buttons can’t trigger slash, so we keep it simple.)

class FundingKoFi(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.kofi_sync.start()

    def cog_unload(self):
        self.kofi_sync.cancel()

    # ---------- Helpers ----------

    async def _refresh_sticky(self, guild: discord.Guild):
        doc = await get_or_create_month(guild.id)
        pool = await funding_pool.find_one({"guild_id": guild.id}) or {"prize_pool_cents": 0}
        ch_id, msg_id = doc.get("channel_id"), doc.get("sticky_message_id")
        if not ch_id or not msg_id:
            return
        try:
            ch = guild.get_channel(int(ch_id)) or await guild.fetch_channel(int(ch_id))
            msg = await ch.fetch_message(int(msg_id))
        except Exception:
            return
        emb = make_embed(doc, guild, pool.get("prize_pool_cents", 0))
        view = FundingView(doc.get("recurring_url"), doc.get("onetime_url"))
        try:
            await msg.edit(embed=emb, view=view)
        except Exception:
            pass

    async def _apply_overflow_to_pool(self, guild_id: int, prev_total: int, new_total: int, goal: int):
        if new_total <= goal:
            return
        # Only add the *incremental* overflow to the pool
        prev_over = max(0, prev_total - goal)
        new_over  = max(0, new_total - goal)
        inc = new_over - prev_over
        if inc > 0:
            await funding_pool.update_one(
                {"guild_id": guild_id},
                {"$inc": {"prize_pool_cents": inc}},
                upsert=True
            )

    # ---------- Slash commands ----------

    @slash_command(guild_ids=[GUILD_ID], name="fund", description="Funding & Prize Pool")
    async def fund_root(self, ctx: discord.ApplicationContext):
        await ctx.respond("Use subcommands: setup, mycode, refresh, set-goal, set-links, add, prizepool reset", ephemeral=True)

    @fund_root.subcommand(name="setup", description="Create/attach the monthly sticky in a channel.")
    async def fund_setup(
        self,
        ctx: discord.ApplicationContext,
        channel: Annotated[discord.TextChannel, Option(discord.TextChannel, "Channel")],
        goal_eur: Annotated[float, Option(float, "Monthly goal in EUR", required=False)] = 10.0,
        recurring_url: Annotated[str | None, Option(str, "Ko-fi membership URL", required=False)] = None,
        onetime_url: Annotated[str | None, Option(str, "Ko-fi one-time URL", required=False)] = None,
        lock_channel: Annotated[bool, Option(bool, "Lock channel for messages?", required=False)] = True,
    ):
        await ctx.defer(ephemeral=True)
        doc = await get_or_create_month(ctx.guild.id)
        # Save settings
        upd = {"goal_cents": eur_to_cents(goal_eur)}
        if recurring_url: upd["recurring_url"] = recurring_url
        if onetime_url:   upd["onetime_url"] = onetime_url

        # Create sticky if missing
        if not doc.get("sticky_message_id") or doc.get("channel_id") != channel.id:
            emb = make_embed(doc | upd, ctx.guild, (await funding_pool.find_one({"guild_id": ctx.guild.id}) or {}).get("prize_pool_cents", 0))
            view = FundingView(upd.get("recurring_url") or doc.get("recurring_url"), upd.get("onetime_url") or doc.get("onetime_url"))
            msg = await channel.send(embed=emb, view=view)
            upd["channel_id"] = channel.id
            upd["sticky_message_id"] = msg.id

        await funding_months.update_one({"guild_id": ctx.guild.id, "month": doc["month"]}, {"$set": upd})

        # Lock channel (optional)
        if lock_channel:
            try:
                overwrites = channel.overwrites_for(ctx.guild.default_role)
                overwrites.send_messages = False
                await channel.set_permissions(ctx.guild.default_role, overwrite=overwrites)
            except Exception:
                pass

        await ctx.followup.send("Funding sticky ready ✅", ephemeral=True)

    @fund_root.subcommand(name="mycode", description="Get your personal Ko-fi link code to earn the Arena Vanguard role.")
    async def fund_mycode(self, ctx: discord.ApplicationContext):
        token = f"VANG-{ctx.author.id:x}-{os.urandom(2).hex()}"
        await funding_tokens.update_one(
            {"token": token}, {"$set": {"guild_id": ctx.guild.id, "user_id": ctx.author.id, "created": datetime.now(timezone.utc)}}, upsert=True
        )
        msg = (
            f"Copy this code and paste it in the **message** field when you donate on Ko-fi:\n"
            f"```{token}```\n"
            "Once your support is processed, I’ll automatically grant you the **Arena Vanguard** role.\n"
            "Tip: use the buttons in the funding channel to open Ko-fi."
        )
        await ctx.respond(msg, ephemeral=True)

    @fund_root.subcommand(name="refresh", description="Refresh the sticky embed now.")
    async def fund_refresh(self, ctx: discord.ApplicationContext):
        await self._refresh_sticky(ctx.guild)
        await ctx.respond("Refreshed ✅", ephemeral=True)

    @fund_root.subcommand(name="set-goal", description="Set monthly goal (EUR).")
    async def fund_set_goal(self, ctx: discord.ApplicationContext, amount_eur: Annotated[float, Option(float, "EUR")]):
        doc = await get_or_create_month(ctx.guild.id)
        await funding_months.update_one({"guild_id": ctx.guild.id, "month": doc["month"]}, {"$set": {"goal_cents": eur_to_cents(amount_eur)}})
        await self._refresh_sticky(ctx.guild)
        await ctx.respond(f"Goal set to €{amount_eur:.2f} ✅", ephemeral=True)

    @fund_root.subcommand(name="set-links", description="Update Ko-fi buttons.")
    async def fund_set_links(
        self,
        ctx: discord.ApplicationContext,
        recurring_url: Annotated[str, Option(str, "Membership URL")],
        onetime_url: Annotated[str, Option(str, "One-time URL")],
    ):
        doc = await get_or_create_month(ctx.guild.id)
        await funding_months.update_one(
            {"guild_id": ctx.guild.id, "month": doc["month"]},
            {"$set": {"recurring_url": recurring_url, "onetime_url": onetime_url}},
        )
        await self._refresh_sticky(ctx.guild)
        await ctx.respond("Links updated ✅", ephemeral=True)

    @fund_root.subcommand(name="add", description="Owner-only: manually add a donation (EUR).")
    async def fund_add(self, ctx: discord.ApplicationContext, amount_eur: Annotated[float, Option(float, "EUR")], note: Annotated[str, Option(str, "Note", required=False)] = ""):
        if ctx.author.id != OWNER_ID:
            return await ctx.respond("Only the owner can use this.", ephemeral=True)

        doc = await get_or_create_month(ctx.guild.id)
        prev_total = doc.get("total_cents", 0)
        goal = doc.get("goal_cents", default_goal_cents())
        inc = eur_to_cents(amount_eur)
        new_total = prev_total + inc

        await funding_months.update_one(
            {"guild_id": ctx.guild.id, "month": doc["month"]},
            {"$inc": {"total_cents": inc},
             "$push": {"donations": {"amount_cents": inc, "source": "manual", "note": note, "ts": datetime.now(timezone.utc)}}}
        )
        await self._apply_overflow_to_pool(ctx.guild.id, prev_total, new_total, goal)
        await self._refresh_sticky(ctx.guild)
        await ctx.respond(f"Added €{amount_eur:.2f} ✅", ephemeral=True)

    @fund_root.subcommand(name="prizepool", description="Owner-only prize pool actions")
    async def fund_pool_group(self, ctx: discord.ApplicationContext):
        await ctx.respond("Use subcommand: reset", ephemeral=True)

    @fund_pool_group.subcommand(name="reset", description="Owner-only: reset prize pool to €0.00")
    async def fund_pool_reset(self, ctx: discord.ApplicationContext):
        if ctx.author.id != OWNER_ID:
            return await ctx.respond("Only the owner can use this.", ephemeral=True)
        await funding_pool.update_one({"guild_id": ctx.guild.id}, {"$set": {"prize_pool_cents": 0}}, upsert=True)
        await self._refresh_sticky(ctx.guild)
        await ctx.respond("Prize Pool reset ✅", ephemeral=True)

    # ---------- Ko-fi sync (API polling, no webhooks server needed) ----------

    @tasks.loop(minutes=5)
    async def kofi_sync(self):
        await self.bot.wait_until_ready()
        token = os.getenv("KOFI_API_TOKEN")
        if not token:
            return
        url = f"https://api.ko-fi.com/v1/supporters?token={token}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=20) as r:
                    data = await r.json()
        except Exception:
            return

        # Ko-fi returns recent supporters; normalize shape
        supporters = data.get("data") or data  # depending on API variant
        if not isinstance(supporters, list):
            return

        # Process per-guild (single guild in your case)
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return

        doc = await get_or_create_month(guild.id)
        seen: set[str] = set(doc.get("seen_txn_ids") or [])
        prev_total = doc.get("total_cents", 0)
        goal = doc.get("goal_cents", default_goal_cents())

        new_txn_ids = []
        incr_total = 0

        # Build token index for fast matching
        tokens = {}
        async for t in funding_tokens.find({"guild_id": guild.id}):
            tokens[t["token"]] = t["user_id"]

        for sup in supporters:
            # Fields: 'kofi_transaction_id', 'amount', 'currency', 'timestamp', 'message', 'type', 'is_public', 'is_subscription_payment'
            txn = str(sup.get("kofi_transaction_id") or "")
            if not txn or txn in seen:
                continue
            # Accept donations + subscription payments
            typ = (sup.get("type") or "").lower()
            is_sub = bool(sup.get("is_subscription_payment"))
            if typ not in ("donation", "subscription", "shoporder", "commission") and not is_sub:
                continue

            currency = (sup.get("currency") or "EUR").upper()
            if currency != "EUR":
                continue  # keep it simple; you can add FX later

            try:
                amount_cents = eur_to_cents(float(sup.get("amount") or 0))
            except Exception:
                amount_cents = 0
            if amount_cents <= 0:
                continue

            # Try to link to a user via token in message
            msg = sup.get("message") or ""
            linked_user_id = None
            for token_str, user_id in tokens.items():
                if token_str in msg:
                    linked_user_id = int(user_id)
                    break

            # Record donation
            donation_row = {
                "amount_cents": amount_cents,
                "source": "kofi-sub" if (is_sub or typ == "subscription") else "kofi",
                "ts": datetime.now(timezone.utc),
                "kofi_txn": txn,
                "linked_user_id": linked_user_id,
            }
            await funding_months.update_one(
                {"guild_id": guild.id, "month": doc["month"]},
                {"$push": {"donations": donation_row}}
            )

            incr_total += amount_cents
            new_txn_ids.append(txn)

            # Grant role if we matched the token
            if linked_user_id:
                await give_role(guild, linked_user_id)

        if incr_total or new_txn_ids:
            new_total = prev_total + incr_total
            await self._apply_overflow_to_pool(guild.id, prev_total, new_total, goal)
            # Save totals and seen ids
            await funding_months.update_one(
                {"guild_id": guild.id, "month": doc["month"]},
                {"$inc": {"total_cents": incr_total}, "$addToSet": {"seen_txn_ids": {"$each": new_txn_ids}}}
            )
            await self._refresh_sticky(guild)

    @kofi_sync.before_loop
    async def _before_kofi(self):
        await self.bot.wait_until_ready()

def setup(bot):
    bot.add_cog(FundingKoFi(bot))
