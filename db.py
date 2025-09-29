# db.py
import os
import motor.motor_asyncio
from config import MONGO_URI, IS_DEV
from pymongo import IndexModel, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

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


# ---------- counters helpers (safe + conflict-free) ----------

async def get_max_match_id() -> int:
    """Return the current maximum match_id in `matches` (0 if none)."""
    doc = await matches.find_one(
        sort=[("match_id", -1)],
        projection={"_id": 0, "match_id": 1},
    )
    return int(doc["match_id"]) if doc and doc.get("match_id") is not None else 0


async def set_counter_to_max_match_id():
    """
    Ensure counters.match_id.sequence_value <= max(match_id) in `matches`.
    Create the doc if missing. Safe under concurrent calls.
    (Two-step update avoids modifier path conflicts.)
    """
    max_existing = await get_max_match_id()

    # Step 1: ensure the doc exists (no $min here to avoid path conflicts)
    try:
        await counters.update_one(
            {"_id": "match_id"},
            {"$setOnInsert": {"sequence_value": max_existing}},
            upsert=True,
        )
    except DuplicateKeyError:
        # Another writer created it first — ignore.
        pass

    # Step 2: lower the counter if it's above the max (no upsert)
    await counters.update_one(
        {"_id": "match_id"},
        {"$min": {"sequence_value": max_existing}},
        upsert=False,
    )


# ---------- deletion helper ----------

async def delete_match_cascade(match_id: int) -> int:
    """
    Delete a match and any related docs; returns number of matches deleted (0 or 1).
    Extend here if you add more match-scoped collections.
    """
    await individual_results.delete_many({"match_id": match_id})
    res = await matches.delete_one({"match_id": match_id})
    return res.deleted_count or 0


# ---------- legacy helper kept for completeness ----------

async def set_counter_to_max_match_id_legacy():
    """
    Older name kept for reference. Prefer set_counter_to_max_match_id().
    """
    return await set_counter_to_max_match_id()
