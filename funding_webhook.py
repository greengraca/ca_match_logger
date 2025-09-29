# funding_webhook.py
import os, json, re, asyncio, datetime as dt
from aiohttp import web

# You’ll inject these from main after bot & db are available
BOT = None
DB = None
GUILD_ID = None
ROLE_NAME = os.getenv("FUND_ROLE_NAME", "Arena Vanguard")
GOAL = float(os.getenv("FUND_GOAL", "10"))
CURRENCY = os.getenv("FUND_CURRENCY", "EUR")
VERIFY = os.getenv("KOFI_VERIFICATION_TOKEN")

CODE_REGEX = re.compile(r"\b[Vv][Aa][Nn][Gg]-[A-Za-z0-9\-]{6,}\b")

def month_key(ts_utc: str | None = None) -> str:
    # Ko-fi timestamp example: "2025-09-29T13:21:05Z"
    if ts_utc:
        t = dt.datetime.fromisoformat(ts_utc.replace("Z","+00:00")).astimezone(dt.timezone.utc)
    else:
        t = dt.datetime.now(dt.timezone.utc)
    return f"{t.year:04d}-{t.month:02d}"

async def ensure_status_doc():
    col = DB.funding_status
    doc = await col.find_one({"_id":"current"})
    if not doc:
        await col.insert_one({
            "_id":"current",
            "month": month_key(),
            "raised": 0.0,
            "goal": GOAL,
            "overflow_pool": 0.0,
            "progress_message_id": None,
            "progress_channel_id": int(os.getenv("FUND_CHANNEL_ID","0")) or None,
        })

async def add_amount_and_rollover(amount: float, ts: str):
    col = DB.funding_status
    doc = await col.find_one({"_id":"current"})
    if not doc:
        await ensure_status_doc()
        doc = await col.find_one({"_id":"current"})

    now_month = month_key(ts)
    if doc["month"] != now_month:
        overflow = max(0.0, float(doc["raised"]) - float(doc["goal"]))
        await col.update_one({"_id":"current"}, {"$set":{
            "month": now_month,
            "raised": 0.0,
            # add any leftover above goal to the prize pool
            "overflow_pool": float(doc.get("overflow_pool",0.0)) + overflow,
        }})
        doc = await col.find_one({"_id":"current"})

    await col.update_one({"_id":"current"}, {"$inc": {"raised": float(amount)}})

async def set_prize_pool(value: float):
    await DB.funding_status.update_one({"_id":"current"}, {"$set":{"overflow_pool": float(value)}})

def make_bar(raised: float, goal: float, width: int = 20) -> str:
    pct = 0.0 if goal <= 0 else min(1.0, raised/goal)
    filled = max(0, min(width, int(round(pct * width))))
    return "█" * filled + "░" * (width - filled), int(pct*100)

async def update_progress_embed():
    await ensure_status_doc()
    status = await DB.funding_status.find_one({"_id":"current"})
    channel_id = status.get("progress_channel_id")
    msg_id = status.get("progress_message_id")
    if not channel_id:
        return

    guild = BOT.get_guild(GUILD_ID)
    if not guild:
        return
    channel = guild.get_channel(channel_id)
    if not channel:
        return

    raised = float(status.get("raised",0.0))
    goal = float(status.get("goal",GOAL))
    pool = float(status.get("overflow_pool",0.0))
    bar, pct = make_bar(raised, goal)

    embed = {
        "title": "Community Fund — Monthly Goal",
        "description": (
            f"**Goal:** {goal:.2f} {CURRENCY}\n"
            f"**Raised:** {raised:.2f} {CURRENCY}  ({pct}%)\n"
            f"`{bar}`\n\n"
            f"Overflow after hitting 100% goes into **Prize Pool**.\n"
            f"**Prize Pool:** {pool:.2f} {CURRENCY}\n\n"
            f"💚 Prefer **Recurring Support** to keep the lights on.\n"
            f"Or make a **One-time** contribution.\n"
            f"Use `/fund mycode` and paste it in your Ko-fi message once to auto-get the role."
        ),
        "color": 0x00BFFF
    }

    components = [
        # (Optional) You can add your Ko-fi links here with discord.ui in bot code.
    ]

    try:
        if msg_id:
            msg = await channel.fetch_message(msg_id)
            await msg.edit(embed=discord.Embed.from_dict(embed), view=None)
        else:
            msg = await channel.send(embed=discord.Embed.from_dict(embed), view=None)
            await DB.funding_status.update_one({"_id":"current"}, {"$set":{"progress_message_id": msg.id}})
    except Exception:
        # If edit fails, fall back to sending a new one
        msg = await channel.send(embed=discord.Embed.from_dict(embed), view=None)
        await DB.funding_status.update_one({"_id":"current"}, {"$set":{"progress_message_id": msg.id}})

async def handle_code_and_role(payload: dict):
    """Grant role if a /fund mycode token was included in Ko-fi message."""
    message = (payload.get("message") or "")[:500]
    if not message:
        return

    m = CODE_REGEX.search(message)
    if not m:
        return
    code = m.group(0)

    row = await DB.kofi_codes.find_one({"code": code})
    if not row:
        return  # unknown code; ignore silently

    user_id = int(row["user_id"])
    # mark code used (optional)
    await DB.kofi_codes.update_one({"_id": row["_id"]}, {"$set": {"used_at": dt.datetime.utcnow()}})

    guild = BOT.get_guild(GUILD_ID)
    if not guild:
        return
    member = guild.get_member(user_id) or await guild.fetch_member(user_id)
    if not member:
        return

    role = discord.utils.get(guild.roles, name=ROLE_NAME)
    if not role:
        role = await guild.create_role(name=ROLE_NAME, color=discord.Color.green(), mentionable=True)
    if role not in member.roles:
        try:
            await member.add_roles(role, reason="Ko-fi donation detected via code")
        except Exception:
            pass  # log if you want

routes = web.RouteTableDef()

@routes.post("/kofi-webhook")
async def kofi_webhook(request: web.Request):
    # Ko-fi posts application/x-www-form-urlencoded with a 'data' field containing JSON
    form = await request.post()
    raw = form.get("data")
    if not raw:
        return web.Response(status=400, text="missing data")

    try:
        payload = json.loads(raw)
    except Exception:
        return web.Response(status=400, text="bad json")

    if VERIFY and payload.get("verification_token") != VERIFY:
        return web.Response(status=401, text="bad token")

    # Basic shape
    amount = float(payload.get("amount", "0") or 0)
    currency = payload.get("currency", CURRENCY)
    timestamp = payload.get("timestamp")  # ISO8601 Z

    # (Optional) only count your server currency
    if currency != CURRENCY:
        # You can skip or convert; here we skip
        return web.Response(status=200, text="ok (ignored other currency)")

    # Persist raw event for audit
    await DB.kofi_events.insert_one({"payload": payload, "received_at": dt.datetime.utcnow()})

    # Update tallies
    if amount > 0:
        await add_amount_and_rollover(amount, timestamp)
        await update_progress_embed()

    # Try to grant donor role if message has a /fund code
    await handle_code_and_role(payload)
    return web.Response(status=200, text="ok")

async def start_web_app(loop=None):
    app = web.Application()
    app.add_routes(routes)
    port = int(os.getenv("PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
