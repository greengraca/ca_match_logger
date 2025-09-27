# db.py
import os
import motor.motor_asyncio
from config import MONGO_URI, IS_DEV

_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)

# If the URI includes a database (e.g., ...mongodb.net/<dbname>?...), use it.
# Otherwise, fall back to an env var, and finally to a sensible default per env.
_default_name = "camatchlogger_dev" if IS_DEV else "camatchlogger"
_db_name = os.getenv("MONGO_DB_NAME", _default_name)

try:
    db = _client.get_default_database() or _client[_db_name]
except Exception:
    db = _client[_db_name]

# Collections (unchanged)
decks = db.decks
matches = db.matches
counters = db.counters
individual_results = db.individual_results

async def ping():
    """Check MongoDB connectivity."""
    try:
        await _client.admin.command("ping")
        return True
    except Exception as e:
        raise e