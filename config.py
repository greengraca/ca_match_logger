# config.py
from dotenv import load_dotenv; load_dotenv()
import os

ENV = os.getenv("ENV", "production").lower()
IS_DEV = ENV == "development"

def _req(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v

DISCORD_BOT_TOKEN = _req("DISCORD_BOT_TOKEN")
MONGO_URI = _req("MONGO_URI_MATCH_LOGGER")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
GUILD_ID = int(_req("GUILD_ID"))  

PRIVATE_CHANNEL_ID = int(os.getenv("PRIVATE_CHANNEL_ID")) if os.getenv("PRIVATE_CHANNEL_ID") else None
ENV = os.getenv("ENV", "production").lower()
IS_DEV = ENV == "development"
MOXFIELD_USER_AGENT = os.getenv("MOXFIELD_USER_AGENT")