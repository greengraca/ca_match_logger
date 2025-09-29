from __future__ import annotations

import os, re, json, asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import discord
from discord.commands import SlashCommandGroup, option
from discord.ext import commands, tasks

from config import GUILD_ID, IS_DEV
from db import db  # Motor database handle

# === Mongo collections ===
funding_months   = db.funding_months
funding_pool     = db.funding_pool
funding_tokens   = db.funding_tokens

# === Config ===
OWNER_ID      = 399635760254550026
ROLE_NAME     = os.getenv("FUND_ROLE_NAME", "ARENA VANGUARD")
SUPPORT_CH_ID = int(os.getenv("FUND_CHANNEL_ID", "0") or 0)        # 🤝support-commander-arena
INBOX_CH_ID   = int(os.getenv("KOFI_INBOX_CHANNEL_ID", "0") or 0)  # 🌐kofi-inbox (webhook posts land here)
KOFI_URL      = os.getenv("KOFI_URL") or "https://ko-fi.com/commanderarena"
MBWAY_PHONE   = os.getenv("MBWAY_PHONE") or "913 574 872"
KOFI_DEBUG = os.getenv("KOFI_DEBUG", "0") == "1"

CODE_RE = re.compile(r"\bVANG-[A-Fa-f0-9x\-]{6,}\b")

EXPLAINER_TEXT = (
    "Hey everyone! \n\n"
    "Running this bot costs about €10/month.\n"
    "We’ve always split it between the moderators for over a year, but since many of you asked, we set up a way for the community to chip in.\n\n"
    "Even if we don’t reach 100%, the mods will cover the rest as always. If we go over the goal for the month, the extra rolls into our **Prize Pool** for the next big tournament.\n\n"
    "As a thank-you, supporters get the role **ARENA VANGUARD** to show you’re an awesome Commander Arena supporter :green_heart:.\n\n"
    ":arrow_right: **How to get the role automatically**: \n"
    "- run `/fund mycode`\n"
    "- copy your code, and paste it in the message field on Ko-fi when you make your donation\n"
    "*The bot matches it and grants your role automatically.*\n\n"
    "**Ko-fi** is the easiest: monthly or one-time on the page.\n"
    "**MB WAY** is also available — tap the button for details.\n\n"
    "------------------------------------------\n"
)


def month_key(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"

def eur_to_cents(x: float) -> int:
    return max(0, int(round(x * 100)))

def cents_to_eur(c: int) -> str:
    return f"{c/100:.2f}"

def make_bar(progress: float, width: int = 24) -> str:
    filled = max(0, min(width, int(round(progress * width))))
    return "▰" * filled + "▱" * (width - filled)

def default_goal_cents() -> int:
    return eur_to_cents(float(os.getenv("FUND_GOAL", "10")))

# ENV: FUND_RATES='{"EUR":1.0,"USD":0.93,"GBP":1.17}'
def to_eur_cents(amount_str: str | float | None, currency: str) -> int:
    try:
        amount = float(amount_str or 0)
    except Exception:
        return 0
    try:
        rates = json.loads(os.getenv("FUND_RATES", '{"EUR":1.0}'))
    except Exception:
        rates = {"EUR": 1.0}
    rate = float(rates.get((currency or "EUR").upper(), 0))
    return max(0, int(round(amount * rate * 100)))


async def ensure_role(guild: discord.Guild) -> Optional[discord.Role]:
    role = discord.utils.get(guild.roles, name=ROLE_NAME)
    if role:
        return role
    try:
        return await guild.create_role(
            name=ROLE_NAME,
            reason="Supporter role for Ko-fi donors",
            color=discord.Color.green(),
            mentionable=True,
        )
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

async def last_month_doc(guild_id: int) -> Optional[Dict[str, Any]]:
    return await funding_months.find_one({"guild_id": guild_id}, sort=[("month", -1)])

async def get_or_create_month(guild_id: int) -> Dict[str, Any]:
    mk = month_key()
    doc = await funding_months.find_one({"guild_id": guild_id, "month": mk})
    if doc:
        return doc

    await funding_pool.update_one(
        {"guild_id": guild_id},
        {"$setOnInsert": {"guild_id": guild_id, "prize_pool_cents": 0}},
        upsert=True
    )

    prev = await last_month_doc(guild_id)
    doc = {
        "guild_id": guild_id,
        "month": mk,
        "goal_cents": default_goal_cents(),
        "total_cents": 0,
        "channel_id": (prev or {}).get("channel_id") or SUPPORT_CH_ID or None,
        "explainer_message_id": (prev or {}).get("explainer_message_id"),   # NEW
        "sticky_message_id": (prev or {}).get("sticky_message_id"),
        "kofi_url": (prev or {}).get("kofi_url") or KOFI_URL,
        "seen_txn_ids": [],
        "donations": [],
    }
    await funding_months.insert_one(doc)
    return await funding_months.find_one({"guild_id": guild_id, "month": mk})


def make_embed(doc: Dict[str, Any], pool_cents: int) -> discord.Embed:
    goal = int(doc.get("goal_cents", default_goal_cents()))
    total = int(doc.get("total_cents", 0))
    pct = 0.0 if goal <= 0 else min(1.0, total / goal)
    bar = make_bar(pct)
    mk = doc.get("month")

    desc = (
        f"**This month:** €{cents_to_eur(total)} / €{cents_to_eur(goal)}  ({int(pct*100)}%)\n"
        f"`{bar}`\n\n"
        f"**Prize Pool:** €{cents_to_eur(pool_cents)}"
    )
    color = 0xFF0000 if IS_DEV else 0x00FF00
    return discord.Embed(title=f"Commander Arena — Monthly Goal ({mk})", description=desc, color=color)


class FundingView(discord.ui.View):
    """Persistent view: Ko-fi (green preferred) + MB WAY button, both ephemeral helpers."""
    def __init__(self, kofi_url: Optional[str], mbway_phone: Optional[str]):
        super().__init__(timeout=None)
        self.kofi_url = kofi_url or KOFI_URL
        self.mbway_phone = mbway_phone or MBWAY_PHONE

    @discord.ui.button(label="💚 Support on Ko-fi", style=discord.ButtonStyle.success, custom_id="fund:kofi")
    async def kofi_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        text = (
            f"Open Ko-fi → {self.kofi_url}\n\n"
            "You can choose **monthly** or **one-time** on the Ko-fi page.\n"
            "Don’t forget: run `/fund mycode` and paste that code in your Ko-fi **message** once "
            "to get **ARENA VANGUARD** automatically."
        )
        await interaction.response.send_message(text, ephemeral=True, delete_after=120)

    @discord.ui.button(label="📱 MB WAY (PT)", style=discord.ButtonStyle.secondary, custom_id="fund:mbway")
    async def mbway_btn(self, button: discord.ui.Button, interaction: discord.Interaction):
        msg = (
            "**MB WAY (Portugal)**\n"
            f"Send to: **`{self.mbway_phone}`**\n"
            "Amount: **any value ≥ €1**\n"
            f"Message: your Discord name (so we can assign your **{ROLE_NAME}** role).\n"
            "If the role isn’t added within 24h, ping a mod or open a ticket. Thanks! 💚"
        )
        await interaction.response.send_message(msg, ephemeral=True, delete_after=120)


# ----- checks & permissions helpers -----
def owner_only():
    def predicate(ctx: discord.ApplicationContext):
        return ctx.author.id == OWNER_ID
    return commands.check(predicate)

# ===========================================================
#                         COG
# ===========================================================
class FundingKoFi(commands.Cog):
    """Ko-fi funding via Cloudflare→Discord webhook. Single sticky + role grant."""

    def __init__(self, bot):
        self.bot = bot
        self._view_registered = False  # register persistent view on first on_ready only
        self._sticky_lock = asyncio.Lock()
        self.monthly_tick.start()

    def cog_unload(self):
        self.monthly_tick.cancel()

    # ---------- register view & ensure sticky when bot is ready ----------
    @commands.Cog.listener()
    async def on_ready(self):
        if not self._view_registered:
            self.bot.add_view(FundingView(KOFI_URL, MBWAY_PHONE))
            self._view_registered = True

        guild = self.bot.get_guild(GUILD_ID)
        if guild:
            # make sure the role exists regardless of donations
            await ensure_role(guild)
            await self._ensure_sticky_exists(guild)


    # Slash groups (py-cord)
    fund = SlashCommandGroup("fund", "Help Commander Arena by funding our bot", guild_ids=[GUILD_ID])
    prizepool = fund.create_subgroup("prizepool", "Owner-only prize pool actions")

    # ------ helpers ------
    
    async def _ensure_explainer_exists(self, guild: discord.Guild, doc: Dict[str, Any]) -> None:
        ch_id = doc.get("channel_id") or SUPPORT_CH_ID
        if not ch_id:
            return
        try:
            ch = guild.get_channel(int(ch_id)) or await guild.fetch_channel(int(ch_id))
        except Exception:
            return

        msg_id = doc.get("explainer_message_id")
        if msg_id:
            try:
                msg = await ch.fetch_message(int(msg_id))
                if msg.content != EXPLAINER_TEXT:
                    await msg.edit(content=EXPLAINER_TEXT)
                return
            except Exception:
                pass  # fall through and recreate if missing

        # Post explainer first so it stays *above* the embed chronologically
        expl = await ch.send(EXPLAINER_TEXT)
        await funding_months.update_one(
            {"guild_id": guild.id, "month": doc["month"]},
            {"$set": {"explainer_message_id": expl.id, "channel_id": ch.id}}
        )


    async def _ensure_sticky_exists(self, guild: discord.Guild):
        async with self._sticky_lock:
            doc = await get_or_create_month(guild.id)

            # 1) Ensure explainer message (top)
            await self._ensure_explainer_exists(guild, doc)

            # 2) Ensure/edit the sticky embed message (below)
            ch_id = doc.get("channel_id") or SUPPORT_CH_ID
            if not ch_id:
                return
            try:
                ch = guild.get_channel(int(ch_id)) or await guild.fetch_channel(int(ch_id))
            except Exception:
                return

            pool = await funding_pool.find_one({"guild_id": guild.id}) or {"prize_pool_cents": 0}
            emb = make_embed(doc, int(pool.get("prize_pool_cents", 0)))
            view = FundingView(doc.get("kofi_url") or KOFI_URL, MBWAY_PHONE)

            msg = None
            msg_id = doc.get("sticky_message_id")
            if msg_id:
                try:
                    msg = await ch.fetch_message(int(msg_id))
                except Exception:
                    msg = None

            if msg is None:
                try:
                    async for m in ch.history(limit=10):
                        if m.author.id == self.bot.user.id and m.embeds:
                            e = m.embeds[0]
                            if e.title and e.title.startswith("Commander Arena — Monthly Goal"):
                                msg = m
                                await funding_months.update_one(
                                    {"guild_id": guild.id, "month": doc["month"]},
                                    {"$set": {"channel_id": ch.id, "sticky_message_id": m.id}}
                                )
                                break
                except Exception:
                    pass

            if msg:
                try:
                    await msg.edit(embed=emb, view=view)
                except Exception:
                    pass
            else:
                msg = await ch.send(embed=emb, view=view)
                await funding_months.update_one(
                    {"guild_id": guild.id, "month": doc["month"]},
                    {"$set": {"channel_id": ch.id, "sticky_message_id": msg.id}}
                )


    async def _refresh_sticky(self, guild: discord.Guild):
        await self._ensure_sticky_exists(guild)

    async def _apply_overflow_to_pool(self, guild_id: int, prev_total: int, new_total: int, goal: int):
        if new_total <= goal:
            return
        prev_over = max(0, prev_total - goal)
        new_over  = max(0, new_total - goal)
        inc = new_over - prev_over
        if inc > 0:
            await funding_pool.update_one(
                {"guild_id": guild_id},
                {"$inc": {"prize_pool_cents": inc}},
                upsert=True
            )

    # ------ commands ------
    # Public
    @fund.command(name="mycode", description="Get your personal code; paste it in Ko-fi message to auto-get the role.", dm_permission=False)
    async def fund_mycode(self, ctx: discord.ApplicationContext):
        token = f"VANG-{ctx.author.id:x}-{os.urandom(2).hex()}"
        await funding_tokens.update_one(
            {"guild_id": ctx.guild.id, "user_id": ctx.author.id},
            {"$set": {"token": token, "created": datetime.now(timezone.utc)}},
            upsert=True
        )
        msg = (
            "Copy this code and paste it in the **message** field when you donate on Ko-fi:\n"
            f"```{token}```\n"
            f"Once Ko-fi notifies us, you’ll automatically receive **{ROLE_NAME}**.\n"
            f"Prefer MB WAY? Use the button in the funding channel."
        )
        await ctx.respond(msg, ephemeral=True)




    # Mods only (visible to members with Manage Server)
    @fund.command(
        name="refresh",
        description="Refresh the sticky embed now.",
        default_member_permissions=discord.Permissions(manage_guild=True),
        dm_permission=False,
    )
    async def fund_refresh(self, ctx: discord.ApplicationContext):
        await self._refresh_sticky(ctx.guild)
        await ctx.respond("Refreshed ✅", ephemeral=True)

    # Owner-only (hide from most by requiring Administrator, plus hard check)
    @fund.command(
        name="set-goal",
        description="Set monthly goal (EUR).",
        default_member_permissions=discord.Permissions(administrator=True),
        dm_permission=False,
    )
    @owner_only()
    @option("amount_eur", float, description="EUR")
    async def fund_set_goal(self, ctx: discord.ApplicationContext, amount_eur: float):
        doc = await get_or_create_month(ctx.guild.id)
        await funding_months.update_one(
            {"guild_id": ctx.guild.id, "month": doc["month"]},
            {"$set": {"goal_cents": eur_to_cents(amount_eur)}}
        )
        await self._refresh_sticky(ctx.guild)
        await ctx.respond(f"Goal set to €{amount_eur:.2f} ✅", ephemeral=True)

    @fund.command(
        name="add",
        description="Owner-only: manually add a donation (EUR).",
        default_member_permissions=discord.Permissions(administrator=True),
        dm_permission=False,
    )
    @owner_only()
    @option("amount_eur", float, description="EUR")
    @option("supporter", discord.Member, description="Member to credit & grant role (optional)", required=False, default=None)
    @option("note", str, description="Note", required=False, default="")
    async def fund_add(self, ctx: discord.ApplicationContext, amount_eur: float, supporter: Optional[discord.Member], note: str):
        doc = await get_or_create_month(ctx.guild.id)
        prev_total = int(doc.get("total_cents", 0))
        goal = int(doc.get("goal_cents", default_goal_cents()))
        inc = eur_to_cents(amount_eur)
        new_total = prev_total + inc

        await funding_months.update_one(
            {"guild_id": ctx.guild.id, "month": doc["month"]},
            {
                "$inc": {"total_cents": inc},
                "$push": {
                    "donations": {
                        "amount_cents": inc,
                        "source": "manual",
                        "note": note,
                        "ts": datetime.now(timezone.utc),
                        "linked_user_id": supporter.id if supporter else None,
                    }
                },
            },
        )

        await self._apply_overflow_to_pool(ctx.guild.id, prev_total, new_total, goal)

        # Grant role if a supporter was specified
        role_msg = ""
        if supporter:
            role = await ensure_role(ctx.guild)
            if role:
                try:
                    # Only add if they don't already have it
                    if role in supporter.roles:
                        role_msg = f" (supporter {supporter.mention} already had {ROLE_NAME})"
                    else:
                        await supporter.add_roles(role, reason="Manual donation supporter")
                        role_msg = f" (granted **{ROLE_NAME}** to {supporter.mention})"
                except Exception as e:
                    role_msg = f" (couldn't add role to {supporter.mention})"

        await self._refresh_sticky(ctx.guild)

        base = f"Added €{amount_eur:.2f}"
        if supporter:
            base += f" for {supporter.mention}"
        await ctx.respond(f"{base} ✅{role_msg}", ephemeral=True)


    @prizepool.command(
        name="reset",
        description="Owner-only: reset prize pool to €0.00",
        default_member_permissions=discord.Permissions(administrator=True),
        dm_permission=False,
    )
    @owner_only()
    async def fund_pool_reset(self, ctx: discord.ApplicationContext):
        await funding_pool.update_one({"guild_id": ctx.guild.id}, {"$set": {"prize_pool_cents": 0}}, upsert=True)
        await self._refresh_sticky(ctx.guild)
        await ctx.respond("Prize Pool reset ✅", ephemeral=True)


    # ------ consume Cloudflare→Discord webhook posts ------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Only handle webhook posts in the Ko-fi inbox channel
        if not INBOX_CH_ID or message.channel.id != INBOX_CH_ID:
            return
        if not message.webhook_id:
            return

        m = re.search(r"```json\s*([\s\S]+?)\s*```", message.content or "")
        if not m:
            return
        try:
            payload = json.loads(m.group(1))
        except Exception:
            return

        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return
        doc = await get_or_create_month(guild.id)

        # de-dupe by Ko-fi transaction id
        txn = str(payload.get("kofi_transaction_id") or "")
        if txn and txn in (doc.get("seen_txn_ids") or []):
            if not KOFI_DEBUG:
                try: await message.delete()
                except Exception: pass
            return

        # only EUR for now
        currency = (payload.get("currency") or "EUR").upper()
        inc = to_eur_cents(payload.get("amount"), currency)
        if inc <= 0:
            if not KOFI_DEBUG:
                try: await message.delete()
                except Exception: pass
            return


        # amount -> cents
        try:
            inc = int(round(float(payload.get("amount") or 0) * 100))
        except Exception:
            inc = 0
        if inc <= 0:
            if not KOFI_DEBUG:
                try: await message.delete()
                except Exception: pass
            return

        # try to match a user token in the Ko-fi message
        linked_user_id: Optional[int] = None
        msg_text = payload.get("message") or ""
        cm = CODE_RE.search(msg_text)
        if cm:
            token = cm.group(0)
            row = await funding_tokens.find_one({"token": token, "guild_id": guild.id})
            if row:
                linked_user_id = int(row["user_id"])
                # (optional) mark token used:
                await funding_tokens.update_one({"token": token}, {"$set": {"used": True, "used_at": datetime.now(timezone.utc)}})
                
        # fallback: Ko-fi linked account
        if not linked_user_id:
            duid = (payload.get("discord_userid") or "").strip()
            if duid.isdigit():
                linked_user_id = int(duid)

        prev_total = int(doc.get("total_cents", 0))
        goal = int(doc.get("goal_cents", default_goal_cents()))
        new_total = prev_total + inc

        updates: Dict[str, Any] = {
            "$inc": {"total_cents": inc},
            "$push": {"donations": {
                "amount_cents": inc,
                "source": "kofi" if not payload.get("is_subscription_payment") else "kofi-sub",
                "ts": datetime.now(timezone.utc),
                "kofi_txn": txn,
                "linked_user_id": linked_user_id,
                "orig_amount": payload.get("amount"),
                "orig_currency": currency,
            }},
        }
        if txn:
            updates["$addToSet"] = {"seen_txn_ids": txn}

        await funding_months.update_one({"guild_id": guild.id, "month": doc["month"]}, updates)
        await self._apply_overflow_to_pool(guild.id, prev_total, new_total, goal)

        if linked_user_id:
            await give_role(guild, linked_user_id)

        await self._refresh_sticky(guild)

        # keep inbox clean in prod
        if not KOFI_DEBUG:
            try:
                await message.delete()
            except Exception:
                pass

    # Keep sticky fresh & ensure doc exists
    @tasks.loop(hours=6)
    async def monthly_tick(self):
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(GUILD_ID)
        if guild:
            await self._ensure_sticky_exists(guild)

    @monthly_tick.before_loop
    async def _before_tick(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)  # small delay to avoid racing manual setup

def setup(bot):
    bot.add_cog(FundingKoFi(bot))
