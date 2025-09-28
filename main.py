# main.py
import logging, discord
from config import DISCORD_BOT_TOKEN, LOG_LEVEL, GUILD_ID, IS_DEV
from db import ping, ensure_indexes  

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.reactions = True
intents.members = True

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("ca_match_logger")

# 👇 this registers commands ONLY in your guild automatically
bot = discord.Bot(intents=intents, debug_guilds=[GUILD_ID])

# load cogs
bot.load_extension("cogs.decks")
bot.load_extension("cogs.matches")
bot.load_extension("cogs.stats")
bot.load_extension("cogs.leaderboard")
bot.load_extension("cogs.funstuff")
bot.load_extension("cogs.admin")
bot.load_extension("cogs.general")
bot.load_extension("timerCog")

_did_indexes = False  # <-- optional one-time guard

@bot.event
async def on_ready():
    global _did_indexes

    try:
        await ping()
        log.info("MongoDB ping OK")
    except Exception as e:
        log.warning("MongoDB ping failed: %s", e)

    # ensure indexes (once)
    if not _did_indexes:
        try:
            await ensure_indexes()
            log.info("DB indexes ensured")
            _did_indexes = True
        except Exception as e:
            log.exception("ensure_indexes() failed: %s", e)

    await bot.change_presence(activity=discord.Game("(DEV) CA Match Logger" if IS_DEV else "CA Match Logger"))
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)

bot.run(DISCORD_BOT_TOKEN)
