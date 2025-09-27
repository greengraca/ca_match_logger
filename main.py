import logging, discord
from config import DISCORD_BOT_TOKEN, LOG_LEVEL, GUILD_ID, IS_DEV
from db import ping

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.reactions = True
intents.members = True

bot = discord.Bot(intents=intents, debug_guilds=[GUILD_ID])

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("ca_match_logger")

@bot.event
async def on_ready():
    await bot.sync_commands(guild_ids=[GUILD_ID])
    try:
        await ping()
        log.info("MongoDB ping OK")
    except Exception as e:
        log.warning("MongoDB ping failed: %s", e)
    await bot.change_presence(activity=discord.Game("(DEV) CA Match Logger" if IS_DEV else "CA Match Logger"))
    log.info("Logged in as %s", bot.user)

# Load cogs
bot.load_extension("cogs.decks")
bot.load_extension("cogs.matches")
bot.load_extension("cogs.stats")
bot.load_extension("cogs.leaderboard")
bot.load_extension("cogs.funstuff")
bot.load_extension("cogs.admin")
bot.load_extension("timerCog")

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
