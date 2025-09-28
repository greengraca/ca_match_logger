import os
import motor.motor_asyncio
from config import MONGO_URI, IS_DEV
from pymongo import IndexModel, ASCENDING, DESCENDING  # <-- add this

_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)

_default_name = "camatchlogger_dev" if IS_DEV else "camatchlogger"
_db_name = os.getenv("MONGO_DB_NAME", _default_name)

try:
    db = _client.get_default_database() or _client[_db_name]
except Exception:
    db = _client[_db_name]

# Collections
decks = db.decks
matches = db.matches
counters = db.counters
individual_results = db.individual_results

async def ping():
    """Check MongoDB connectivity."""
    await _client.admin.command("ping")
    return True

async def ensure_indexes():
    # matches
    await matches.create_indexes([
        IndexModel([("match_id", ASCENDING)], unique=True, name="uniq_match_id"),
    ])

    # individual_results
    await individual_results.create_indexes([
        IndexModel([("match_id", ASCENDING), ("player_id", ASCENDING)], name="ir_match_player"),
    ])

    # decks (if you don't have dupes; otherwise clean before making it unique)
    await decks.create_indexes([
        IndexModel([("name", ASCENDING)], unique=True, name="uniq_deck_name"),
    ])

async def set_counter_to_max_match_id():
    doc = await matches.find_one(sort=[("match_id", DESCENDING)], projection={"match_id": 1})
    new_seq = int(doc["match_id"]) if doc and doc.get("match_id") is not None else 0
    await counters.update_one({"_id": "match_id"}, {"$set": {"sequence_value": new_seq}}, upsert=True)

async def dec_counter_if_latest(deleted_match_id: int):
    """
    Decrement the counter only if the deleted match was the latest issued ID.
    This preserves monotonic IDs and avoids collisions.
    """
    try:
        deleted = int(deleted_match_id)
    except Exception:
        return
    await counters.update_one(
        {"_id": "match_id", "sequence_value": deleted},
        {"$inc": {"sequence_value": -1}}
    )
