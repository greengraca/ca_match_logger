# main.py
import logging, os, discord
print(f"Discord version: {discord.__version__}")
print(f"Discord module path: {discord.__file__}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("ca_match_logger")


def load_opus() -> bool:
    if discord.opus.is_loaded():
        log.info("Opus already loaded")
        return True

    here = os.path.dirname(os.path.abspath(__file__))

    candidates = [
        # Windows (put libopus.dll next to main.py)
        "libopus.dll", "libopus-0.dll", "opus.dll",
        os.path.join(here, "libopus.dll"),
        os.path.join(here, "libopus-0.dll"),
        os.path.join(here, "opus.dll"),

        # Linux (Heroku will provide these via Aptfile)
        "libopus.so.0", "libopus.so",

        # macOS
        "libopus.dylib",
    ]

    for path in candidates:
        try:
            discord.opus.load_opus(path)
            log.info("Loaded Opus from %s", path)
            return True
        except Exception:
            continue

    log.warning("Opus library not found; voice will NOT work.")
    return False

load_opus()

from config import DISCORD_BOT_TOKEN, LOG_LEVEL, GUILD_ID, IS_DEV
from db import ping, ensure_indexes


# --- Load Opus defensively (voice) ---
try:
    if not discord.opus.is_loaded():
        for cand in ("libopus.so.0", "libopus", "opus"):
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

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.reactions = True
intents.members = True
intents.voice_states = True       

bot = discord.Bot(intents=intents, debug_guilds=[GUILD_ID])

# Load cogs
bot.load_extension("cogs.decks")
bot.load_extension("cogs.matches")
bot.load_extension("cogs.stats")
bot.load_extension("cogs.leaderboard")
bot.load_extension("cogs.funstuff")
bot.load_extension("cogs.admin")
bot.load_extension("cogs.general")
bot.load_extension("cogs.events")
bot.load_extension("cogs.funding_kofi")
bot.load_extension("timerCog")

_did_indexes = False

@bot.event
async def on_ready():
    print(f"[boot] voice_states intent on? {bot.intents.voice_states}")
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

@bot.event
async def on_voice_state_update(member, before, after):
    if member.id == bot.user.id:
        print(f"[voice] self state: {getattr(before.channel,'id',None)} -> {getattr(after.channel,'id',None)}")
        
bot.run(DISCORD_BOT_TOKEN)
