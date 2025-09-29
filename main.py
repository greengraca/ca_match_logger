# main.py
import os
import asyncio
import logging
import discord

from config import DISCORD_BOT_TOKEN, LOG_LEVEL, GUILD_ID, IS_DEV
from db import ping, ensure_indexes  # keep your helpers
import funding_webhook as kofi       # <-- import the module, not symbols

# ----- logging -----
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("ca_match_logger")

# ----- load Opus defensively (voice) -----
try:
    if not discord.opus.is_loaded():
        for cand in ("libpulse-simple.so.0", "libopus.so.0", "libopus", "opus"):
            try:
                discord.opus.load_opus(cand)
                log.info("Loaded Opus: %s", cand)
                break
            except OSError:
                continue
        else:
            log.warning("Opus library not found; voice will NOT work.")
except Exception as e:
    log.warning("Opus init error: %s", e)

# ----- discord intents / bot -----
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.reactions = True
intents.members = True

bot = discord.Bot(intents=intents, debug_guilds=[GUILD_ID])

# ----- load cogs -----
bot.load_extension("cogs.decks")
bot.load_extension("cogs.matches")
bot.load_extension("cogs.stats")
bot.load_extension("cogs.leaderboard")
bot.load_extension("cogs.funstuff")
bot.load_extension("cogs.admin")
bot.load_extension("cogs.general")
bot.load_extension("cogs.events")
bot.load_extension("cogs.funding-kofi")
bot.load_extension("timerCog")

_did_indexes = False

@bot.event
async def on_ready():
    global _did_indexes
    try:
        await ping()
        log.info("MongoDB ping OK")
    except Exception as e:
        log.warning("MongoDB ping failed: %s", e)

    if not _did_indexes:
        try:
            await ensure_indexes()
            log.info("DB indexes ensured")
            _did_indexes = True
        except Exception as e:
            log.exception("ensure_indexes() failed: %s", e)

    await bot.change_presence(activity=discord.Game("(DEV) CA Match Logger" if IS_DEV else "CA Match Logger"))
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)

async def main():
    # ---- Wire Ko-fi webhook module with bot, DB and guild id ----
    kofi.BOT = bot
    kofi.GUILD_ID = GUILD_ID

    # Provide a Motor DB handle to the webhook module.
    # If your `db.py` already exposes a global `db`, use that:
    try:
        from db import db as motor_db  # <-- adjust if your module exposes it differently
        kofi.DB = motor_db
    except Exception:
        # Fallback: create a client from env (requires MONGODB_URI in env)
        from motor.motor_asyncio import AsyncIOMotorClient
        uri = os.getenv("MONGODB_URI")
        if not uri:
            log.error("MONGODB_URI is not set; Ko-fi webhook DB will be unavailable.")
        else:
            kofi.DB = AsyncIOMotorClient(uri).get_default_database()

    # ---- Start the aiohttp server for Ko-fi webhooks (listens on $PORT) ----
    try:
        await kofi.start_web_app()
        log.info("Ko-fi webhook server started")
    except Exception as e:
        log.exception("Failed to start Ko-fi webhook server: %s", e)

    # ---- Start the Discord bot (blocks until shutdown) ----
    await bot.start(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
