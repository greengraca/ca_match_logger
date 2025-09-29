# db.py
import os
import motor.motor_asyncio
from config import MONGO_URI, IS_DEV
from pymongo import IndexModel, ASCENDING, DESCENDING

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
event_registrations = db.event_registrations

# Funding collections
funding_months = db.funding_months
funding_pool = db.funding_pool
funding_tokens = db.funding_tokens


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
        IndexModel([("player_id", ASCENDING), ("date", DESCENDING)], name="ir_player_date_desc"),
        IndexModel([("deck_name", ASCENDING), ("date", DESCENDING)], name="ir_deck_date_desc"),
    ])

    # decks (ensure no dupes first if you make it unique)
    await decks.create_indexes([
        IndexModel([("name", ASCENDING)], unique=True, name="uniq_deck_name"),
    ])

    # event_registrations
    await event_registrations.create_indexes([
        IndexModel([("event_id", ASCENDING), ("user_id", ASCENDING)], unique=True, name="uniq_event_user"),
        IndexModel([("event_id", ASCENDING)], name="by_event"),
    ])

    # ----- Funding indexes -----
    # One document per (guild_id, month)
    await funding_months.create_indexes([
        IndexModel([("guild_id", ASCENDING), ("month", ASCENDING)], unique=True, name="uniq_guild_month"),
        IndexModel([("sticky_message_id", ASCENDING)], name="by_sticky_msg"),
    ])

    # One row per guild (accumulator)
    await funding_pool.create_indexes([
        IndexModel([("guild_id", ASCENDING)], unique=True, name="uniq_pool_guild"),
    ])

    # Tokens:
    #  - token must be unique
    #  - we keep a non-unique index on (guild_id, user_id) for lookups
    await funding_tokens.create_indexes([
        IndexModel([("token", ASCENDING)], unique=True, name="uniq_token"),
        IndexModel([("guild_id", ASCENDING), ("user_id", ASCENDING)], name="by_guild_user"),
        IndexModel([("created", DESCENDING)], name="by_created_desc"),
    ])


async def set_counter_to_max_match_id():
    """
    Set counters.match_id.sequence_value to max(match_id) from `matches`.
    Lowers the counter only if it's currently greater than that max.
    If the doc doesn't exist, upsert it with the computed value.
    """
    doc = await matches.find_one(
        sort=[("match_id", -1)],
        projection={"_id": 0, "match_id": 1}
    )
    max_existing = int(doc["match_id"]) if doc and doc.get("match_id") is not None else 0

    # Only lower if the counter is above max_existing.
    # The filter prevents moving backwards if another process already advanced it.
    await counters.update_one(
        {"_id": "match_id", "sequence_value": {"$gt": max_existing}},
        {"$set": {"sequence_value": max_existing}},
        upsert=True,
    )
