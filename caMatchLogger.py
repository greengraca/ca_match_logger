import discord
from discord.commands import option
from discord.ext import commands
import datetime
from datetime import datetime, timezone, timedelta
import logging
from dotenv import load_dotenv
import os
import motor.motor_asyncio
import aiohttp
import re
import asyncio
import random
import difflib
from rapidfuzz import fuzz, process
# from rapidfuzz.distance import Levenshtein
import time
# from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from functools import wraps
from discord import Embed, Interaction, Option
from pymongo import DESCENDING
from collections import defaultdict
from difflib import get_close_matches
# from discord.ui import Modal, TextInput

load_dotenv()


mongo_uri = os.getenv('MONGO_URI_MATCH_LOGGER')
token = os.getenv('DISCORD_BOT_TOKEN')
guild_id = 690232443718074471 # Replace this with your actual guild ID


logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s:%(levelname)s:%(name)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    handlers=[
                        logging.FileHandler("bot.log"),  # Log to a file
                        logging.StreamHandler()  # Log to standard output
                    ])

logger = logging.getLogger(__name__)


clientDB = motor.motor_asyncio.AsyncIOMotorClient(mongo_uri)
db = clientDB.camatchlogger

async def ping_server():
  try:
      await clientDB.admin.command('ping')
      print("Pinged your deployment. You successfully connected to MongoDB!")
  except Exception as e:
      print(e)
      
      

decks_on_database = db.decks
matches = db.matches
counters = db.counters
individual_results = db.individual_results

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.reactions = True  # Necessary for on_reaction_add and on_reaction_remove events
intents.members = True  # Necessary for member-related information, useful in many cases

bot = discord.Bot(debug_guilds=[690232443718074471], intents=intents)
bot.load_extension("timerCog")



# Replace with the actual ID of your "Private-commands" channel
private_commands_channel_id = 1299454152140918848

def ephemeral_in_private_channel(command_func):
    @wraps(command_func)
    async def wrapper(ctx, *args, **kwargs):
        # Check if the command is in the Private-commands channel
        if ctx.channel_id == private_commands_channel_id:
            # Add `ephemeral=True` to `ctx.respond()` calls within the command
            kwargs['ephemeral'] = True
        # Call the original command function with updated arguments
        return await command_func(ctx, *args, **kwargs)
    return wrapper

def capitalize_words(deck_name: str) -> str:
    # Words that should not be capitalized
    lowercase_words = {"and", "but"}
    
    # Check if the deck name contains a "/"
    if "/" in deck_name:
        return deck_name
    
    # Split the deck name into words
    words = deck_name.split()
    
    # Capitalize each word unless it's in the lowercase_words set
    capitalized_words = [word.capitalize() if word.lower() not in lowercase_words else word.lower() for word in words]
    
    # Join the words back together into a single string
    return " ".join(capitalized_words)


@bot.slash_command(guild_ids=[690232443718074471], name='abegasiosinterasios', description='Lists how much interaction Abegão has in his deck.')
# @ephemeral_in_private_channel
async def abegasios_interasios(ctx):
    ephemeral = ctx.channel.id == 1299454152140918848

    url = "https://api.moxfield.com/v2/decks/all/Jnfr7xWDIkWEOPHEQ4MPAw"
    counters = ["Fierce Guardianship", "Tishana\u0027s Tidebinder", "Otawara, Soaring City", "Manglehorn", "Subtlety", "Dispel", "Force of Will", "Delay", "Mana Drain", "Flusterstorm", "Spell Pierce", "Muddle the Mixture", "Miscast", "Trickbind", "Misdirection", "Swan Song", "An Offer You Can\u0027t Refuse", "Mental Misstep", "Mindbreak Trap", "Force of Negation", "Spell Snare", "Stern Scolding", "Pact of Negation", "Pyroblast", "Red Elemental Blast", "Blue Elemental Blast"]
    bounces = ["Snap", "Cyclonic Rift", "Alchemist\u0027s Retrieval", "Chain of Vapor"]
    removal = ["Legolas\u0027s Quick Reflexes", "Archdruid\u0027s Charm", "Boseiju, Who Endures"]
    
    counters_set = set(counters)
    bounces_set = set(bounces)
    removal_set = set(removal)

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                mainboard = data.get("mainboard", {})
                card_names = [card_name for card_name in mainboard.keys()]
                
                counters_count = 0
                bounces_count = 0
                removal_count = 0
                
                for card_name in card_names:
                    if card_name in counters_set:
                        counters_count += 1
                    elif card_name in bounces_set:
                        bounces_count += 1
                    elif card_name in removal_set:
                        removal_count += 1
                
                interacao = f"Counters: {counters_count}\n" \
                    f"Bounces: {bounces_count}\n" \
                    f"Removal: {removal_count}\n" \
                        
                embed = discord.Embed(title="Abegão's Interaction", description=interacao, color=0x00ff00)

                await ctx.respond(embed=embed, ephemeral=ephemeral)
            else:
                await ctx.respond("Failed to fetch deck information.", ephemeral=ephemeral)


async def fetch_and_format_decks():
    decks = []
    async for deck in decks_on_database.find():
        # decks.append(deck["name"])
        formatted_deck_name = capitalize_words(deck["name"])
        decks.append(formatted_deck_name)
    return decks



@bot.slash_command(guild_ids=[690232443718074471], name='listdecks', description='Lists all the decks in the database.')
# @ephemeral_in_private_channel
async def list_decks(ctx):
    ephemeral = ctx.channel.id == 1299454152140918848
    decks = await fetch_and_format_decks()  # Assuming this returns a list of deck names
    
    # Sort the decks alphabetically
    sorted_decks = sorted(decks, key=str.lower)  # Sorts the deck names case-insensitively
    
    # Create the embed
    embed = discord.Embed(title="Decks in the database", description="\n".join(sorted_decks), color=0x00ff00)
    
    await ctx.respond(embed=embed, ephemeral=ephemeral)

    
    

async def deck_autocomplete(ctx: discord.AutocompleteContext):

    cursor = decks_on_database.find({}, {'name': 1, '_id': 0})  # Query all documents but only retrieve the 'name' field
    all_decks = [document['name'] async for document in cursor]
    
    return [deck for deck in all_decks if ctx.value.lower() in deck.lower()]


async def misnamed_deck_autocomplete(ctx: discord.AutocompleteContext):
    # Fetch all valid deck names from the 'decks' collection
    valid_decks_cursor = decks_on_database.find({}, {'name': 1, '_id': 0})
    valid_decks = set([document['name'] async for document in valid_decks_cursor])

    # Fetch distinct deck names from 'individual_results' and 'matches'
    individual_results_cursor = await individual_results.distinct("deck_name")
    matches_cursor = await matches.distinct("players.deck_name")

    # Combine results into a single set
    logged_decks = set(individual_results_cursor).union(set(matches_cursor))

    # Find decks that are in the logs but not in the valid decks collection
    misnamed_decks = logged_decks - valid_decks

    # Filter the misnamed decks based on the user's input in the autocomplete context
    return [deck for deck in misnamed_decks if ctx.value.lower() in deck.lower()]


async def insert_match_result(match_details):
    # match_details is a dict containing the match_id, players, and date
    await matches.insert_one(match_details)
    
    # Insert individual results for each player
    for player in match_details['players']:
        individual_result = {
            "player_id": player['player_id'],
            "deck_name": player['deck_name'],
            "seat": player['position'],
            "result": player['result'],
            "match_id": match_details['match_id'],
            "date": match_details['date']
        }
        await individual_results.insert_one(individual_result)
    
    for player in match_details['players']:
    # Check if the player already exists in the deck
        playerExists = await decks_on_database.find_one({'name': player['deck_name'], 'players.player_id': player['player_id']})
        if not playerExists:
        # Player does not exist, add them to the deck
            await decks_on_database.update_one(
                {'name': player['deck_name']},
                {'$addToSet': {'players': {'player_id': player['player_id'], 'wins': 0, 'losses': 0, 'draws': 0}}}
            )

        # Update each player's performance in the decks collection
        
        if player['result'] == 'win':
            result_field = 'players.$.wins'
        elif player['result'] == 'loss':
            result_field = 'players.$.losses'
        elif player['result'] == 'draw':
            result_field = 'players.$.draws'
        else:
            # Handle unexpected result value
            continue

        await decks_on_database.update_one(
            {'name': player['deck_name'], 'players.player_id': player['player_id']},
            {'$inc': {result_field: 1}},
        )


async def get_next_match_id():
    await db.counters.find_one_and_update(
        {"_id": "match_id"},
        {"$inc": {"sequence_value": 1}},
        upsert=True
    )
    result = await db.counters.find_one({"_id": "match_id"})
    return result["sequence_value"] if result else 1


@bot.slash_command(guild_ids=[690232443718074471], name='track', description='Tracks a game match.')
@option("deck1", autocomplete=deck_autocomplete)
@option("deck2", autocomplete=deck_autocomplete)
@option("deck3", autocomplete=deck_autocomplete)
@option("deck4", autocomplete=deck_autocomplete)
@option("winner", autocomplete=discord.utils.basic_autocomplete(["Player 1", "Player 2", "Player 3", "Player 4", "Draw"]))
async def track(ctx: discord.ApplicationContext, player1: discord.Member, deck1: str, player2: discord.Member, deck2: str, player3: discord.Member, deck3: str, player4: discord.Member, deck4: str, winner: str):
    logger.info("Track command invoked")
    try:
        # Check if decks exist in the database
        decks = [deck1, deck2, deck3, deck4]
        invalid_decks = []
        for deck in decks:
            if not await deck_exists(deck):  # Calls your existing deck validation function
                invalid_decks.append(deck)

        if invalid_decks:
            # Notify the user which decks are invalid
            invalid_list = ", ".join([f"`{deck}`" for deck in invalid_decks])
            await ctx.respond(f"The following decks do not exist in the database: {invalid_list}. Please ensure all decks are valid by choosing from the autocomplete before proceeding.", ephemeral=True)
            logger.info(f"Invalid decks provided: {invalid_list}")
            return

        # Proceed with track logic if all decks are valid
        match_id = await get_next_match_id()
        logger.info(f"Obtained match_id: {match_id}")

        # Construct the match details
        match_details = {
            "match_id": match_id,
            "players": [
                {"player_id": player1.id, "deck_name": deck1, "position": 1, "result": "win" if winner == "Player 1" else "loss" if winner != "Draw" else "draw"},
                {"player_id": player2.id, "deck_name": deck2, "position": 2, "result": "win" if winner == "Player 2" else "loss" if winner != "Draw" else "draw"},
                {"player_id": player3.id, "deck_name": deck3, "position": 3, "result": "win" if winner == "Player 3" else "loss" if winner != "Draw" else "draw"},
                {"player_id": player4.id, "deck_name": deck4, "position": 4, "result": "win" if winner == "Player 4" else "loss" if winner != "Draw" else "draw"}
            ],
            "date": datetime.now(timezone.utc)
        }

        # Insert the match result into the database
        await insert_match_result(match_details)
        logger.info(f"Inserted match result for match_id: {match_id}")
        
        # Apply capitalize_words to the deck names for display
        deck1_display = capitalize_words(deck1)
        deck2_display = capitalize_words(deck2)
        deck3_display = capitalize_words(deck3)
        deck4_display = capitalize_words(deck4)

        # Construct response message
        player_mapping = {"Player 1": player1, "Player 2": player2, "Player 3": player3, "Player 4": player4}
        gameData = f"A game with the following players has been logged:\n\n" \
                   f"Game ID: {match_id}\n\n" \
                   f"Player 1: {player1.mention}, playing with {deck1_display}\n" \
                   f"Player 2: {player2.mention}, playing with {deck2_display}\n" \
                   f"Player 3: {player3.mention}, playing with {deck3_display}\n" \
                   f"Player 4: {player4.mention}, playing with {deck4_display}\n\n"
        if winner != "Draw":
            winning_player = player_mapping[winner]  # Use the mapping to get the correct player
            gameData += f"The winner was {winning_player.mention}."
        else:
            gameData += "The game was a draw."
        embed = discord.Embed(title="Game Log", description=gameData, color=0x00ff00)

        await ctx.respond(content=f"Track for Game ID {match_id} has been logged. Ask a MODERATOR to delete if you made a mistake.", embed=embed)
        
        # Access the TimerCog instance to interact with its state
        timer_cog = bot.get_cog("TimerCog")
        if timer_cog is not None:
            user_voice_state = ctx.author.voice
            if user_voice_state and user_voice_state.channel:
                voice_channel_id = user_voice_state.channel.id
                
                # Assuming we want to stop the most recent timer in the channel
                if voice_channel_id in timer_cog.voice_channel_timers:
                    current_timer_sequence = timer_cog.voice_channel_timers[voice_channel_id]
                    timer_id = f"{voice_channel_id}_{current_timer_sequence}"

                    if timer_cog.is_user_in_timer(str(ctx.author.id), timer_id):
                        await timer_cog.set_timer_stopped(timer_id)
                        # await ctx.respond("Timer stopped due to /track command.", ephemeral=True)
                    else:
                        await ctx.respond("You are not part of the active timer's voice channel or no active timer.", ephemeral=True)
                else:
                    await ctx.respond("No active timer found in your voice channel.", ephemeral=True)
        # await ctx.respond("You need to be part of the active timer's voice channel to use /track.", ephemeral=True)
    
        logger.info("Successfully responded to track command.")
    except Exception as e:
        logger.error(f"Error in track command: {e}", exc_info=True)
        await ctx.respond("An error occurred while processing the command.")
        
    except Exception as e:
        logger.error(f"Error in track command: {e}", exc_info=True)
        await ctx.respond("An error occurred while processing the command.")
    
    
async def deck_exists(deck_name):
    existing_deck = await decks_on_database.find_one({"name": deck_name})
    return existing_deck is not None    


def format_deck_name(deck_name):
    parts = deck_name.split("/")
    sorted_parts = sorted(part.strip().capitalize() for part in parts)
    return "/".join(sorted_parts)


@bot.slash_command(guild_ids=[690232443718074471], name='newdeck', description='Add a new deck to the database.')
async def new_deck(ctx, deck: str):
    deck_to_save = format_deck_name(deck)
    display_deck_name = capitalize_words(deck_to_save)

    logger.info("Attempting to add a new deck: %s", deck_to_save)

    # Check if deck already exists
    if await deck_exists(deck_to_save):
        embed = discord.Embed(
            title="Deck already exists",
            description=f"{display_deck_name} is already in the database.",
            color=0xff0000
        )
        logger.warning("Attempted to add a deck that already exists: %s", deck_to_save)
        await ctx.respond(embed=embed)
        return

    # Fetch all existing decks
    existing_decks_cursor = decks_on_database.find({}, {"name": 1})
    existing_decks = [d["name"] for d in await existing_decks_cursor.to_list(length=None)]

    # Similarity and prefix matching
    matches_dict = defaultdict(int)
    for existing in existing_decks:
        score = fuzz.ratio(deck_to_save.lower(), existing.lower())
        if score >= 85:
            matches_dict[existing] = max(matches_dict[existing], score)

    for existing in existing_decks:
        if (deck_to_save.lower().startswith(existing.lower()) or
            existing.lower().startswith(deck_to_save.lower())):
            score = fuzz.ratio(deck_to_save.lower(), existing.lower())
            matches_dict[existing] = max(matches_dict[existing], score)

    matches = sorted(matches_dict.items(), key=lambda x: x[1], reverse=True)

    # If matches found, ask for confirmation
    if matches:
        suggestions = "\n".join([f"- {capitalize_words(m[0])} ({m[1]:.2f}% match)" for m in matches])
        warning_message = (
            f"A similar deck already exists. Are you sure you want to add **{display_deck_name}**?\n\n"
            f"Possible matches:\n{suggestions}"
        )

        confirm_button = discord.ui.Button(label="Yes, add it", style=discord.ButtonStyle.green)
        cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.red)
        view = discord.ui.View(timeout=30)

        async def confirm_callback(interaction):
            await decks_on_database.insert_one({"name": deck_to_save})
            logger.info("User confirmed and added new deck: %s", deck_to_save)

            # Delete the ephemeral confirmation message
            await interaction.response.edit_message(content="✅ Deck confirmed. Adding...", view=None)

            # Send a public confirmation message
            public_embed = discord.Embed(
                title="New deck added",
                description=f"{display_deck_name} was successfully added to the database.",
                color=0x00ff00
            )
            await ctx.channel.send(embed=public_embed)

        async def cancel_callback(interaction):
            embed = discord.Embed(
                title="Deck addition cancelled",
                description="The deck was not added.",
                color=0xffcc00
            )
            await interaction.response.edit_message(content=None, embed=embed, view=None)

        confirm_button.callback = confirm_callback
        cancel_button.callback = cancel_callback
        view.add_item(confirm_button)
        view.add_item(cancel_button)

        await ctx.respond(warning_message, view=view, ephemeral=True)
        return

    # No matches, add normally
    await decks_on_database.insert_one({"name": deck_to_save})
    embed = discord.Embed(
        title="New deck added",
        description=f"{display_deck_name} was successfully added to the database.",
        color=0x00ff00
    )
    logger.info("Added new deck without similarity warning: %s", deck_to_save)
    await ctx.respond(embed=embed)
    
    
class ConfirmDeckCreationView(discord.ui.View):
    def __init__(self, deck_to_save, display_deck_name, ctx, timeout=60):
        super().__init__(timeout=timeout)
        self.deck_to_save = deck_to_save
        self.display_deck_name = display_deck_name
        self.ctx = ctx
        self.value = None

    @discord.ui.button(label="✅ Yes, add anyway", style=discord.ButtonStyle.green)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
        await decks_on_database.insert_one({"name": self.deck_to_save})
        embed = discord.Embed(
            title="New deck added",
            description=f"{self.display_deck_name} was successfully added to the database.",
            color=0x00ff00
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)
        self.value = True
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, button: discord.ui.Button, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Deck creation cancelled.", view=None)
        self.value = False
        self.stop()
        

async def get_period_start_date(period, postban=True):
    """Return the start date based on the selected period and whether it's post-ban or not."""
    now = datetime.now(timezone.utc)
    postban_start_date = datetime(2024, 9, 24, tzinfo=timezone.utc)  # Fixed postban start date

    if postban:
        # Calculate start date relative to now, but not earlier than the postban start date
        if period == "1m":
            start_date = max(now - timedelta(days=30), postban_start_date)
        elif period == "3m":
            start_date = max(now - timedelta(days=90), postban_start_date)
        elif period == "6m":
            start_date = max(now - timedelta(days=180), postban_start_date)
        elif period == "1y":
            start_date = max(now - timedelta(days=365), postban_start_date)
        else:
            start_date = postban_start_date  # Default to postban start date if period is invalid or 'all'
    else:
        # Normal period calculation relative to the current date
        if period == "1m":
            start_date = now - timedelta(days=30)
        elif period == "3m":
            start_date = now - timedelta(days=90)
        elif period == "6m":
            start_date = now - timedelta(days=180)
        elif period == "1y":
            start_date = now - timedelta(days=365)
        else:
            start_date = datetime.min.replace(tzinfo=timezone.utc)  # Default to the earliest possible date for 'all'

    return start_date

async def get_previous_month_date(period):
    now = datetime.now(timezone.utc)
    if period == "1m":
        # Get the date 30 days ago (the start date for the current 1-month period)
        current_period_start = now - timedelta(days=30)
        # Calculate the start date of the previous 30-day period
        previous_period_start = current_period_start - timedelta(days=30)
        previous_period_end = current_period_start
    else:
        # Handle other periods as needed
        previous_period_start = None
        previous_period_end = None

    return previous_period_start, previous_period_end

  
def format_period(period):
    """Return a human-readable string for the given period code."""
    period_mapping = {
        "1m": "Last 30 Days",
        "3m": "Last 3 Months",
        "6m": "Last 6 Months",
        "1y": "Last Year",
        "all": "Eternal"
    }
    # Return the corresponding string or a default if the period code isn't recognized
    return period_mapping.get(period, "Custom Period")


async def fetch_player_stats(player_id, period, postban, deck_filter=None):
    logger.info(f"Fetching player stats for player_id: {player_id} (deck_filter: {deck_filter})")

    start_date = await get_period_start_date(period, postban)

    match_stage = {
        "player_id": player_id,
        "date": {"$gte": start_date}
    }

    if deck_filter:
        match_stage["deck_name"] = {"$regex": f"^{deck_filter}$", "$options": "i"}

    # Top decks (only if no filter)
    top_decks = []
    if not deck_filter:
        pipeline = [
            {"$match": match_stage},
            {"$group": {
                "_id": "$deck_name",
                "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                "draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}},
                "games_played": {"$sum": 1}
            }},
            {"$addFields": {
                "weighted_wins": {"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]},
                "normal_win_percentage": {"$multiply": [{"$divide": ["$wins", "$games_played"]}, 100]},
                "weighted_win_percentage": {"$multiply": [{"$divide": [{"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]}, "$games_played"]}, 100]}
            }},
            {"$sort": {"weighted_win_percentage": -1}},
            {"$limit": 10}
        ]

        async for doc in individual_results.aggregate(pipeline):
            top_decks.append({
                "deck_name": capitalize_words(doc['_id']),
                "wins": doc['wins'],
                "losses": doc['losses'],
                "draws": doc['draws'],
                "games_played": doc['games_played'],
                "win_percentage": doc['normal_win_percentage'],
                "weighted_win_percentage": doc['weighted_win_percentage']
            })

    # Seat stats
    seat_pipeline = [
        {"$match": match_stage},
        {"$group": {
            "_id": None,
            "total_wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
            "total_losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
            "total_draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}},
            "seat1": {"$sum": {"$cond": [{"$eq": ["$seat", 1]}, 1, 0]}},
            "seat2": {"$sum": {"$cond": [{"$eq": ["$seat", 2]}, 1, 0]}},
            "seat3": {"$sum": {"$cond": [{"$eq": ["$seat", 3]}, 1, 0]}},
            "seat4": {"$sum": {"$cond": [{"$eq": ["$seat", 4]}, 1, 0]}},
            "winseat1": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 1]}, {"$eq": ["$result", "win"]}]}, 1, 0]}},
            "winseat2": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 2]}, {"$eq": ["$result", "win"]}]}, 1, 0]}},
            "winseat3": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 3]}, {"$eq": ["$result", "win"]}]}, 1, 0]}},
            "winseat4": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 4]}, {"$eq": ["$result", "win"]}]}, 1, 0]}}
        }}
    ]

    total_stats = await individual_results.aggregate(seat_pipeline).to_list(length=1)
    if not total_stats:
        return None

    stats = total_stats[0]
    result = {
        "wins": stats['total_wins'],
        "losses": stats['total_losses'],
        "draws": stats['total_draws'],
        "seat1": stats['seat1'],
        "seat2": stats['seat2'],
        "seat3": stats['seat3'],
        "seat4": stats['seat4'],
        "winseat1": stats['winseat1'],
        "winseat2": stats['winseat2'],
        "winseat3": stats['winseat3'],
        "winseat4": stats['winseat4'],
        "top_10_decks": top_decks
    }

    # 🧩 Game Dump Join — match by match_id ↔ match_id
    if deck_filter:
        game_docs = await individual_results.aggregate([
            {"$match": {
                **match_stage,
                "match_id": {"$ne": None}
            }},
            {
                "$lookup": {
                    "from": "matches",  # 👈 FIXED HERE
                    "localField": "match_id",
                    "foreignField": "match_id",
                    "as": "game_data"
                }
            },
            {"$unwind": "$game_data"},
            {"$project": {
                "match_id": 1,
                "players": "$game_data.players",
                "date": "$game_data.date"
            }}
        ]).to_list(None)


        game_list = []
        for doc in game_docs:
            game_id = doc['match_id']
            game_players = [
                {
                    "deck_name": capitalize_words(p.get("deck_name", "Unknown")),
                    "winner": p.get("result") == "win"
                }
                for p in doc['players']
            ]
            game_date = doc['date']
            game_list.append({
                "id": game_id,
                "players": game_players,
                "date": game_date
            })

        result["games"] = game_list

    return result

    

MAX_CHARS = 2000
PAGE_HEADER = "**\ud83d\udcdc Full Game Dump:**\n"

class PaginatorView(discord.ui.View):
    def __init__(self, author, pages):
        super().__init__(timeout=60)
        self.author = author
        self.pages = pages
        self.current = 0

    async def send_page(self, interaction):
        if interaction.user != self.author:
            await interaction.response.send_message("You can't interact with this paginator.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"📜 Game Dump (Page {self.current + 1}/{len(self.pages)})",
            description=self.pages[self.current],
            color=0x00ffcc
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.current > 0:
            self.current -= 1
            await self.send_page(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.current < len(self.pages) - 1:
            self.current += 1
            await self.send_page(interaction)

def paginate_entries(entries: list[str]) -> list[str]:
    pages = []
    current_page = PAGE_HEADER
    for entry in entries:
        entry = entry.strip()
        if len(current_page) + len(entry) + 1 > MAX_CHARS:
            pages.append(current_page.strip())
            current_page = PAGE_HEADER + entry + "\n"
        else:
            current_page += entry + "\n"
    if current_page.strip() != PAGE_HEADER.strip():
        pages.append(current_page.strip())
    return pages

@bot.slash_command(name="playerstats", description="Get statistics for a player.")
@option("period", description="Select a period", choices=["1m", "3m", "6m", "1y", "all"])
@option("postban", description="Use post-ban date?", default=True)
@option("individual_deck", description="Filter by specific deck", required=False, autocomplete=deck_autocomplete)
async def playerstats(
    ctx,
    player: discord.Member,
    period: str = "all",
    postban: bool = True,
    individual_deck: str = None
):
    ephemeral = ctx.channel.id == 1299454152140918848
    player_stats = await fetch_player_stats(int(player.id), period, postban, deck_filter=individual_deck)
    readable_period = format_period(period)
    title_suffix = " (POST-BAN)" if postban else ""

    formatted_deck_name = capitalize_words(individual_deck) if individual_deck else ""

    if player_stats:
        total_games = player_stats['wins'] + player_stats['losses'] + player_stats['draws']
        win_percentage = (player_stats['wins'] / total_games) * 100 if total_games > 0 else 0
        weighted_win_percentage = ((player_stats['wins'] + player_stats['draws'] * 0.143) / total_games) * 100 if total_games > 0 else 0

        win_by_seat1_percentage = (player_stats['winseat1'] / player_stats['seat1']) * 100 if player_stats['seat1'] > 0 else 0
        win_by_seat2_percentage = (player_stats['winseat2'] / player_stats['seat2']) * 100 if player_stats['seat2'] > 0 else 0
        win_by_seat3_percentage = (player_stats['winseat3'] / player_stats['seat3']) * 100 if player_stats['seat3'] > 0 else 0
        win_by_seat4_percentage = (player_stats['winseat4'] / player_stats['seat4']) * 100 if player_stats['seat4'] > 0 else 0

        playerStatsMessage = f"**Total Games Played**: {total_games}\n" \
                    f"**{player_stats['wins']}** W | **{player_stats['losses']}** L | **{player_stats['draws']}** D \n\n" \
                    f"**Win %**: {win_percentage:.2f}%\n" \
                    f"**🏋Win %**: {weighted_win_percentage:.2f}%\n\n" \
                    f"**Seating %**: {((player_stats['seat1']/total_games)*100):.0f}% (**{player_stats['seat1']}**) | {((player_stats['seat2']/total_games)*100):.0f}% (**{player_stats['seat2']}**) | {((player_stats['seat3']/total_games)*100):.0f}% (**{player_stats['seat3']}**) | {((player_stats['seat4']/total_games)*100):.0f}% (**{player_stats['seat4']}**)\n" \
                    f"**Win by Seat %**: {win_by_seat1_percentage:.2f}% | {win_by_seat2_percentage:.2f}% | {win_by_seat3_percentage:.2f}% | {win_by_seat4_percentage:.2f}%\n\n"


        if not individual_deck:
            playerStatsMessage += "**Top 10 Decks**:\n"
            for deck in player_stats['top_10_decks']:
                playerStatsMessage += f" - **{deck['deck_name']}** - {deck['wins']} W | {deck['losses']} L | {deck['draws']} D \n" \
                                      f"Games Played: {deck['games_played']}, Win%: {deck['win_percentage']:.2f}%, *🏋Win%: {deck['weighted_win_percentage']:.2f}%*\n\n"

        title = f"Player Stats for {player.display_name} with {formatted_deck_name} - {readable_period}{title_suffix}" if individual_deck else f"Player Stats for {player.display_name} - {readable_period}{title_suffix}"
        embed = discord.Embed(
            title=title,
            description=playerStatsMessage,
            color=0x00ff00
        )

        if individual_deck and 'games' in player_stats:
            games = player_stats['games']
            game_count = len(games)

            if game_count > 5:
                class DumpPaginator(discord.ui.View):
                    def __init__(self, author, pages):
                        super().__init__(timeout=60)
                        self.author = author
                        self.pages = pages
                        self.current = 0

                    async def send_page(self, interaction):
                        if interaction.user != self.author:
                            await interaction.response.send_message("You can't interact with this paginator.", ephemeral=True)
                            return

                        embed = discord.Embed(
                            title=f"📜 Game Dump (Page {self.current + 1}/{len(self.pages)})",
                            description=self.pages[self.current],
                            color=0x00ffcc
                        )
                        await interaction.response.edit_message(embed=embed, view=self)

                    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
                    async def prev(self, button: discord.ui.Button, interaction: discord.Interaction):
                        if self.current > 0:
                            self.current -= 1
                            await self.send_page(interaction)

                    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
                    async def next(self, button: discord.ui.Button, interaction: discord.Interaction):
                        if self.current < len(self.pages) - 1:
                            self.current += 1
                            await self.send_page(interaction)


                class DumpView(discord.ui.View):
                    def __init__(self, author, games, target_deck):
                        super().__init__(timeout=30)
                        self.author = author
                        self.games = games
                        self.target_deck = target_deck.lower() if target_deck else None

                    @discord.ui.button(label="Yes - Show Dump", style=discord.ButtonStyle.success)
                    async def show_dump(self, button: discord.ui.Button, interaction: discord.Interaction):
                        if interaction.user != self.author:
                            await interaction.response.send_message("You can't respond to this.", ephemeral=True)
                            return

                        dump_entries = []

                        for g in self.games:
                            date_obj = g['date']
                            date_str = f"{date_obj.strftime('%b')} {date_obj.day}, {date_obj.year}"
                            game_text = f"**Game ID**: `{g['id']}` - `{date_str}`\n"
                            for i, p in enumerate(g['players']):
                                seat_number = i + 1
                                name = p['deck_name']
                                bolded_name = f"**{name}**" if name.lower() == self.target_deck else name
                                outcome = "🏆" if p['winner'] else ""
                                game_text += f"Seat {seat_number}: {bolded_name} {outcome}\n"
                            dump_entries.append(game_text.strip())

                        # Create embed-sized chunks
                        MAX_CHARS = 4000  # Embed description limit
                        pages = []
                        current = ""

                        for entry in dump_entries:
                            if len(current) + len(entry) + 2 > MAX_CHARS:
                                pages.append(current.strip())
                                current = ""
                            current += entry + "\n\n"

                        if current:
                            pages.append(current.strip())

                        embed = discord.Embed(
                            title=f"📜 Game Dump (Page 1/{len(pages)})",
                            description=pages[0],
                            color=0x00ffcc
                        )

                        view = DumpPaginator(author=interaction.user, pages=pages)
                        await interaction.response.send_message(embed=embed, ephemeral=True, view=view)


                embed.description += f"\n\n🗃 **{game_count} games found with deck '{formatted_deck_name}'**.\nWould you like to see a full dump of the games?"
                view = DumpView(author=ctx.author, games=games, target_deck=individual_deck)
                await ctx.respond(embed=embed, ephemeral=ephemeral, view=view)

                return

            elif game_count > 0:
                dump_msg = "\n**📜 Game Details (≤5 games):**\n"
                for g in games:
                    date_obj = g['date']
                    date_str = f"{date_obj.strftime('%b')} {date_obj.day}, {date_obj.year}"
                    game_text = f"**Game ID**: `{g['id']}` - `{date_str}`\n"
                    for i, p in enumerate(g['players']):
                        seat_number = i + 1
                        name = p['deck_name']
                        bolded_name = f"**{name}**" if name.lower() == (individual_deck or "").lower() else name
                        outcome = "🏆" if p['winner'] else ""
                        game_text += f"Seat {seat_number}: {bolded_name} {outcome}\n"
                    dump_msg += f"{game_text.strip()}\n\n"
                embed.description += dump_msg
            else:
                embed.description += f"\n🗃 0 games found with deck '{formatted_deck_name}'."

        await ctx.respond(embed=embed, ephemeral=ephemeral)

    else:
        if individual_deck:
            await ctx.respond(f"No stats found for {player.display_name} with `{formatted_deck_name}`.", ephemeral=ephemeral)
        else:
            await ctx.respond(f"No stats found for {player.display_name}.", ephemeral=ephemeral)







@bot.slash_command(name="estousempreemultimo", description="Show the last 10 seatings of a player.")
# @ephemeral_in_private_channel
async def estousempreemultimo(ctx, player: discord.Member):
    logger.info(f"Fetching last 10 seatings for player_id: {player.id}")
    ephemeral = ctx.channel.id == 1299454152140918848


    # Fetch the last 10 games where the player participated
    cursor = individual_results.find(
        {"player_id": player.id},
        {"seat": 1, "date": 1, "match_id": 1, "_id": 0}
    ).sort("date", -1).limit(10)
    
    last_10_games = await cursor.to_list(length=10)
    
    if not last_10_games:
        await ctx.respond(f"{player.display_name} doesn't have 10 games.")
        return
    
    # Prepare the message with seating information
    seating_info = ""
    
    # Initialize counters for each seat
    seat1 = 0
    seat2 = 0
    seat3 = 0
    seat4 = 0
    for index, game in enumerate(last_10_games, start=1):
        seating_info += f"**Game {game['match_id']}** - Seat: {game['seat']}\n"

        # Increment the appropriate seat counter
        if game['seat'] == 1:
            seat1 += 1
        elif game['seat'] == 2:
            seat2 += 1
        elif game['seat'] == 3:
            seat3 += 1
        elif game['seat'] == 4:
            seat4 += 1
            
    # Prepare the summary line
    seat_summary = f"**Summary:** **{seat1}** | **{seat2}** | **{seat3}** | **{seat4}**"
    
    # Embed the message
    embed = discord.Embed(
        title=f"Last 10 Seatings for {player.display_name}",
        description=f"{seat_summary}\n\n{seating_info}",
        color=0x00ff00
    )

    await ctx.respond(embed=embed, ephemeral=ephemeral)


@bot.slash_command(name="deckstats", description="Get statistics for a deck.")
@option("deck", description="Select a deck", autocomplete=deck_autocomplete)
@option("period", description="Select a period", choices=["1m", "3m", "6m", "1y", "all"], default="all")
@option("postban", description="Use post-ban date?", default=True)  # Default to postban
# @ephemeral_in_private_channel
async def deckstats(ctx, deck: str, period: str, postban: bool = True):
    ephemeral = ctx.channel.id == 1299454152140918848

    deck_stats = await fetch_deck_stats(deck, period, postban)
    readable_period = format_period(period)  # Convert period code to readable format
    
    # Conditional title based on postban value
    title_suffix = " (POST-BAN)" if postban else ""
    
    if deck_stats:
        total_games = deck_stats['wins'] + deck_stats['losses'] + deck_stats['draws']
        win_percentage = (deck_stats['wins'] / total_games) * 100 if total_games > 0 else 0
        weighted_win_percentage = ((deck_stats['wins'] + deck_stats['draws'] * 0.143) / total_games) * 100 if total_games > 0 else 0
        
        # Calculate Win by Seat % based on the number of games played in each seat
        win_by_seat1_percentage = (deck_stats['winseat1'] / deck_stats['seat1']) * 100 if deck_stats['seat1'] > 0 else 0
        win_by_seat2_percentage = (deck_stats['winseat2'] / deck_stats['seat2']) * 100 if deck_stats['seat2'] > 0 else 0
        win_by_seat3_percentage = (deck_stats['winseat3'] / deck_stats['seat3']) * 100 if deck_stats['seat3'] > 0 else 0
        win_by_seat4_percentage = (deck_stats['winseat4'] / deck_stats['seat4']) * 100 if deck_stats['seat4'] > 0 else 0
        
        deckStatsMessage = f"**Total Games Played:** {total_games}\n" \
                            f"**{deck_stats['wins']}** W | **{deck_stats['losses']}** L | **{deck_stats['draws']}** D\n\n" \
                            f"**Win %:** {win_percentage:.2f}%\n" \
                            f"**🏋Win %**: {weighted_win_percentage:.2f}%\n\n" \
                            f"**Seating %**: {((deck_stats['seat1']/total_games)*100):.0f}% (**{deck_stats['seat1']}**) | {((deck_stats['seat2']/total_games)*100):.0f}% (**{deck_stats['seat2']}**) | {((deck_stats['seat3']/total_games)*100):.0f}% (**{deck_stats['seat3']}**) | {((deck_stats['seat4']/total_games)*100):.0f}% (**{deck_stats['seat4']}**)\n" \
                            f"**Win by Seat %**: {win_by_seat1_percentage:.2f}% | {win_by_seat2_percentage:.2f}% | {win_by_seat3_percentage:.2f}% | {win_by_seat4_percentage:.2f}%\n\n" \
                            "**Top 10 Players:**\n"

        for player in deck_stats['top_10_players']:
            deckStatsMessage += f"- **{player['player_name']}** - " \
                                f"{player['wins']} W | {player['losses']} L | {player['draws']} D - " \
                                f"Games Played: {player['games_played']}, Win%: {player['win_percentage']:.0f}%, 🏋Win%: {player['weighted_win_percentage']:.0f}%\n"

        formatted_deck_name = capitalize_words(deck)

        embed = discord.Embed(title=f"Deck Stats for {formatted_deck_name} - {readable_period}{title_suffix}", description=deckStatsMessage, color=0x00ff00)
        await ctx.respond(embed=embed, ephemeral=ephemeral)
    else:
        await ctx.respond(f"No stats found for deck {deck}.", ephemeral=ephemeral)

async def fetch_deck_stats(deck_name, period, postban):
    start_date = await get_period_start_date(period, postban)
    guild = bot.get_guild(guild_id)  # Get the guild object

    # Aggregation for top 10 players for the deck
    pipeline = [
        {"$match": {"deck_name": deck_name, "date": {"$gte": start_date}}},
        {"$group": {
            "_id": "$player_id",
            "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
            "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
            "draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}},
            "games_played": {"$sum": 1}
        }},
        {"$addFields": {
            "win_percentage": {"$cond": [{"$eq": ["$games_played", 0]}, 0, {"$multiply": [{"$divide": ["$wins", "$games_played"]}, 100]}]},
            "weighted_win_percentage": {
                "$cond": [
                    {"$eq": ["$games_played", 0]},
                    0,
                    {
                        "$multiply": [
                            {
                                "$divide": [
                                    {"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]},
                                    "$games_played"
                                ]
                            },
                            100
                        ]
                    }
                ]
            }
        }},
        {"$match": {"games_played": {"$gte": 5}}},
        {"$sort": {"weighted_win_percentage": -1, "games_played": -1}},  # Sort by win percentage first, then by games played
        {"$limit": 10}
    ]

    top_players = []
    async for doc in individual_results.aggregate(pipeline):
        try:
            member = await guild.fetch_member(doc['_id'])  # Fetch the member from the guild
        except discord.NotFound:
            # Member not found, skip to the next iteration
            continue

        # player = await bot.fetch_user(doc['_id'])  # Assumes player_id stored in individual_results is Discord user ID
        win_percentage = (doc['wins'] / doc['games_played']) * 100 if doc['games_played'] > 0 else 0
        weighted_win_percentage = ((doc['wins'] + doc['draws'] * 0.143) / doc['games_played']) * 100 if doc['games_played'] > 0 else 0
        top_players.append({
            "player_name": member.nick if member.nick else member.display_name,
            "wins": doc['wins'],
            "losses": doc['losses'],
            "draws": doc['draws'],
            "games_played": doc['games_played'],
            "win_percentage": win_percentage,
            "weighted_win_percentage": weighted_win_percentage,
            
        })

    # Aggregate total wins, losses, and draws for the deck
    total_stats_pipeline = [
        {"$match": {"deck_name": deck_name, "date": {"$gte": start_date}}},
        {"$group": {
            "_id": None,
            "total_wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
            "total_losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
            "total_draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}},
            "seat1": {"$sum": {"$cond": [{"$eq": ["$seat", 1]}, 1, 0]}},
            "seat2": {"$sum": {"$cond": [{"$eq": ["$seat", 2]}, 1, 0]}},
            "seat3": {"$sum": {"$cond": [{"$eq": ["$seat", 3]}, 1, 0]}},
            "seat4": {"$sum": {"$cond": [{"$eq": ["$seat", 4]}, 1, 0]}},
            "winseat1": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 1]}, {"$eq": ["$result", "win"]}]}, 1, 0]}},
            "winseat2": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 2]}, {"$eq": ["$result", "win"]}]}, 1, 0]}},
            "winseat3": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 3]}, {"$eq": ["$result", "win"]}]}, 1, 0]}},
            "winseat4": {"$sum": {"$cond": [{"$and": [{"$eq": ["$seat", 4]}, {"$eq": ["$result", "win"]}]}, 1, 0]}}
        }}
    ]

    total_stats = await individual_results.aggregate(total_stats_pipeline).to_list(length=1)

    if total_stats:
        stats = total_stats[0]
        return {
            "wins": stats['total_wins'],
            "losses": stats['total_losses'],
            "draws": stats['total_draws'],
            "seat1": stats['seat1'],
            "seat2": stats['seat2'],
            "seat3": stats['seat3'],
            "seat4": stats['seat4'],
            "winseat1": stats['winseat1'],
            "winseat2": stats['winseat2'],
            "winseat3": stats['winseat3'],
            "winseat4": stats['winseat4'],
            "top_10_players": top_players
        }
    else:
        return None


# Helper function to calculate win percentage
def calculate_win_percentage(wins, games_played):
    return (wins / games_played) * 100 if games_played > 0 else 0


# Define the leaderboard command with a "type" parameter for choosing between player or deck leaderboard
@bot.slash_command(name="leaderboard", description="Show the top leaderboard for players or decks.")
@option("type", description="Select leaderboard type", choices=["players", "decks"])
@option("period", description="Select the period", choices=["1m", "3m", "6m", "1y", "all"], default="3m")
@option("postban", description="Use post-ban date?", default=True)  # Default to postban
# @ephemeral_in_private_channel
async def leaderboard(ctx, type: str, period: str, postban: bool = True):
    # print(f"Ephemeral value in 'leaderboard' command: {ephemeral}")  # Log the value

    if type == "players":
        await show_player_leaderboard(ctx, period, postban)
    elif type == "decks":
        await show_deck_leaderboard(ctx, period, postban)

async def show_player_leaderboard(ctx, period, postban):
    # Set ephemeral based on channel type
    ephemeral = ctx.channel.id == 1299454152140918848
    await ctx.defer(ephemeral=ephemeral)  # Defer the response if processing takes longer
    
    print(f"Inside 'show_player_leaderboard', ephemeral: {ephemeral}")  # Log the ephemeral status
    print(f"Fetching leaderboard for period: {period}")
    start_date = await get_period_start_date(period, postban)
    print(f"Start date for query: {start_date}")
    
    readable_period = format_period(period)  # Convert period code to readable format
    
    # Conditional title based on postban value
    title_suffix = " (POST-BAN)" if postban else ""
    
    # Set the limit based on the period
    result_limit = 40 if period != "1m" else None  # No limit if period is '1m'
    
    if period == "1m":
        previous_start_date, previous_end_date = await get_previous_month_date(period)
    
    pipeline = [
        {"$match": {"date": {"$gte": start_date}}},
        {"$group": {
            "_id": "$player_id",
            "games_played": {"$sum": 1},
            "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
            "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
            "draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}}
        }},
        {"$addFields": {
            "weighted_wins": {"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]},
            "normal_win_percentage": {"$multiply": [{"$divide": ["$wins", "$games_played"]}, 100]},
            "weighted_win_percentage": {"$multiply": [{"$divide": [{"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]}, "$games_played"]}, 100]}
        }},
        {"$match": {"games_played": {"$gte": 15}}},
        {"$sort": {"weighted_win_percentage": -1, "games_played": -1}}
    ]
    
    if result_limit:
        pipeline.append({"$limit": result_limit})  # Add limit stage to the pipeline only if it's not None

    guild = bot.get_guild(guild_id)  # Get the guild object
    
    # List to store multiple embeds
    embeds = []
    embed = discord.Embed(title=f"Player Leaderboard - {readable_period}{title_suffix}", color=0x00ff00)
    position = 1  # Start ranking at 1
    field_count = 0  # Track the number of fields added to the current embed
    
    # Store the results of the current period
    current_period_results = {}

    try:
        async for doc in individual_results.aggregate(pipeline):
            try:
                member = await guild.fetch_member(doc['_id'])
                if member:
                    player_name = member.nick if member.nick else member.display_name
                    
                     # Assign medal emojis for top 3 positions
                    if position == 1:
                        rank_display = "🥇"
                    elif position == 2:
                        rank_display = "🥈"
                    elif position == 3:
                        rank_display = "🥉"
                    else:
                        rank_display = str(position) + "."
                        
                    embed.add_field(
                        name=f"{rank_display} **{player_name}**: {doc['wins']}W | {doc['losses']}L | {doc['draws']}D",
                        value=f"• Win: **{int(doc['normal_win_percentage'])}**% | *🏋Win%: **{int(doc['weighted_win_percentage'])}**%* | (Games: {doc['games_played']}) | ID: {doc['_id']}",
                        inline=False
                    )
                    position += 1
                    field_count += 1
                    
                    # Store current period results for comparison
                    current_period_results[doc['_id']] = doc['weighted_win_percentage']
                    
                    # Check if we reached the max fields per embed
                    if field_count >= 25:
                        embeds.append(embed)  # Save the current embed
                        # Create a new embed for the next set of results
                        embed = discord.Embed(title=f"Player Leaderboard - {readable_period}{title_suffix}", color=0x00ff00)
                        field_count = 0  # Reset field count for the new embed
            except discord.NotFound:
                # Member not found, skip to the next iteration
                print(f"Member {doc['_id']} not found, skipping.")
                continue
            except Exception as e:
                print(f"Failed to fetch or display member {doc['_id']}: {e}")
    except Exception as e:
        print(f"Failed to aggregate results: {e}")
    
    # Add the last embed if it has any fields
    if field_count > 0:
        embeds.append(embed)
        
    
    # If the period is "1m", fetch and compare with the previous month
    if period == "1m":
        previous_pipeline = [
            {"$match": {"date": {"$gte": previous_start_date, "$lt": previous_end_date}}},
            {"$group": {
                "_id": "$player_id",
                "games_played": {"$sum": 1},
                "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                "draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}}
            }},
            {"$addFields": {
                "weighted_wins": {"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]},
                "weighted_win_percentage": {"$multiply": [{"$divide": [{"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]}, "$games_played"]}, 100]}
            }}
        ]

        try:
            async for doc in individual_results.aggregate(previous_pipeline):
                current_weighted_win_percentage = current_period_results.get(doc['_id'])
                if current_weighted_win_percentage is not None:
                    previous_weighted_win_percentage = doc['weighted_win_percentage']
                    improvement = current_weighted_win_percentage - previous_weighted_win_percentage

                    if -1 < improvement < 1:
                        improvement_display = '🔄 0%'
                    else:
                        arrow = '⬆️' if improvement > 0 else '🔻'
                        improvement_display = f"{arrow} {abs(int(improvement))}%"
                    
                    # Update the corresponding field in the embed with the improvement
                    for embed in embeds:
                        for field in embed.fields:
                            # Check if the player's ID is present in the field value
                            if f"ID: {doc['_id']}" in field.value:
                                # Replace the current win percentage with the improvement display
                                field.value = field.value.replace(
                                    f"**{int(current_weighted_win_percentage)}**%*",
                                    f"**{int(current_weighted_win_percentage)}**%* ({improvement_display})"
                                )
                                break  # Stop after finding and updating the relevant field
        except Exception as e:
            print(f"Failed to aggregate previous month results: {e}")
            
    # Remove the deck ID before sending the embed
    for embed in embeds:
        for i, field in enumerate(embed.fields):
            embed.set_field_at(i, name=field.name, value=field.value.split(" | ID:")[0], inline=field.inline)

    # If no fields were added to any embed
    if not embeds:
        embed = discord.Embed(title=f"Player Leaderboard - {readable_period}{title_suffix}", description="No results found.", color=0x00ff00)
        embeds.append(embed)
        
    # Make sure all embeds but the first have no visible title
    for i, embed in enumerate(embeds):
        if i != 0:
            embed.title = None  # Clear the title for all except the first embed

    # Send all embeds in a single message
    await ctx.respond(embeds=embeds, ephemeral=ephemeral)



async def show_deck_leaderboard(ctx, period, postban):
    ephemeral = ctx.channel.id == 1299454152140918848
    await ctx.defer(ephemeral=ephemeral)  # Defer the response if processing takes longer

    print(f"Inside 'show_deck_leaderboard', ephemeral: {ephemeral}")  # Log the ephemeral status

    print(f"Fetching leaderboard for period: {period}")
    start_date = await get_period_start_date(period, postban)
    print(f"Start date for query: {start_date}")
    
    # Conditional title based on postban value
    title_suffix = " (POST-BAN)" if postban else ""
    
    readable_period = format_period(period)  # Convert period code to readable format
    # Set the limit based on the period
    result_limit = 40 if period != "1m" else None  # No limit if period is '1m'
    
    if period == "1m":
        previous_start_date, previous_end_date = await get_previous_month_date(period)
    
    pipeline = [
        {"$match": {
            "date": {"$gte": start_date}
            # "games_played": {"$gte": 15}  # Minimum games played
        }},
        {"$group": {
            "_id": "$deck_name",
            "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
            "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
            "draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}},
            "games_played": {"$sum": 1}
        }},
        {"$addFields": {
            "weighted_wins": {"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]},
            "normal_win_percentage": {"$multiply": [{"$divide": ["$wins", "$games_played"]}, 100]},
            "weighted_win_percentage": {"$multiply": [{"$divide": [{"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]}, "$games_played"]}, 100]}
        }},
        {"$match": {"games_played": {"$gte": 15}}},
        {"$sort": {
            "weighted_win_percentage": -1,  # Sort primarily by weighted win percentage
            "games_played": -1  # Secondary sort by games played
        }},
        # {"$limit": result_limit}
    ]
    
    if result_limit:
        pipeline.append({"$limit": result_limit})  # Add limit stage to the pipeline only if it's not None

    # List to store multiple embeds
    embeds = []
    
    embed = discord.Embed(title=f"Decks Leaderboard - {readable_period}{title_suffix}", color=0x00ff00)
    position = 1  # Start ranking at 1
    field_count = 0  # Track the number of fields added to the current embed

    # Store the results of the current period
    current_period_results = {}

    try:
        async for doc in individual_results.aggregate(pipeline):
            formatted_deck_name = capitalize_words(doc['_id'])
            
            # Assign medal emojis for top 3 positions
            if position == 1:
                rank_display = "🥇"
            elif position == 2:
                rank_display = "🥈"
            elif position == 3:
                rank_display = "🥉"
            else:
                rank_display = str(position) + "."
                        
            embed.add_field(
                name=f"{rank_display} {formatted_deck_name}: {doc['wins']}W | {doc['losses']}L | {doc['draws']}D",
                value=f"• W: **{int(doc['normal_win_percentage'])}**% | *🏋W: **{int(doc['weighted_win_percentage'])}**%* | (Games: {doc['games_played']}) | ID: {doc['_id']}",
                inline=False
            )
            position += 1
            field_count += 1
            
            # Store current period results for comparison
            current_period_results[doc['_id']] = doc['weighted_win_percentage']
            
             # Check if we reached the max fields per embed
            if field_count >= 25:
                embeds.append(embed)  # Save the current embed
                # Create a new embed for the next set of results
                embed = discord.Embed(title=f"Decks Leaderboard - {readable_period} (Part {len(embeds) + 1}){title_suffix}", color=0x00ff00)
                field_count = 0  # Reset field count for the new embed
    except Exception as e:
        print(f"Failed to aggregate results: {e}")
    
     # Add the last embed if it has any fields
    if field_count > 0:
        embeds.append(embed)
    
    # If the period is "1m", fetch and compare with the previous month
    if period == "1m":
        previous_pipeline = [
            {"$match": {"date": {"$gte": previous_start_date, "$lt": previous_end_date}}},
            {"$group": {
                "_id": "$deck_name",
                "games_played": {"$sum": 1},
                "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                "draws": {"$sum": {"$cond": [{"$eq": ["$result", "draw"]}, 1, 0]}},
            }},
            {"$addFields": {
                "weighted_wins": {"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]},
                "weighted_win_percentage": {"$multiply": [{"$divide": [{"$add": ["$wins", {"$multiply": ["$draws", 0.143]}]}, "$games_played"]}, 100]}
            }},
        ]
        
        try:
            async for doc in individual_results.aggregate(previous_pipeline):
                current_weighted_win_percentage = current_period_results.get(doc['_id'])
                if current_weighted_win_percentage is not None:
                    previous_weighted_win_percentage = doc['weighted_win_percentage']
                    improvement = current_weighted_win_percentage - previous_weighted_win_percentage

                    # Handle no significant change (between -1% and 1%)
                    if -1 < improvement < 1:
                        improvement_display = '🔄 0%'
                    else:
                        arrow = '⬆️' if improvement > 0 else '🔻'
                        improvement_display = f"{arrow} {abs(int(improvement))}%"
                    
                    # Update the corresponding field in the embed with the improvement
                    for embed in embeds:
                        for field in embed.fields:
                            if f"ID: {doc['_id']}" in field.value:
                                field.value = field.value.replace(
                                    f"**{int(current_weighted_win_percentage)}**%*",
                                    f"**{int(current_weighted_win_percentage)}**%* ({improvement_display})"
                                )
                                break
        except Exception as e:
            print(f"Failed to aggregate previous month results: {e}")
            
    # Remove the deck ID before sending the embed
    for embed in embeds:
        for i, field in enumerate(embed.fields):
            embed.set_field_at(i, name=field.name, value=field.value.split(" | ID:")[0], inline=field.inline)

    # If no fields were added to any embed
    if not embeds:
        embed = discord.Embed(title=f"Decks Leaderboard - {readable_period}{title_suffix}", description="No results found.", color=0x00ff00)
        embeds.append(embed)

    # Make sure all embeds but the first have no visible title
    for i, embed in enumerate(embeds):
        if i != 0:
            embed.title = None  # Clear the title for all except the first embed

    # Send all embeds in a single message
    await ctx.respond(embeds=embeds, ephemeral=ephemeral)


def extract_game_id(message_content):
    match = re.search(r"Game ID (\d+)", message_content)
    return int(match.group(1)) if match else None

def user_has_permission(user, guild):
    # Assuming "user" is a Member object and "guild" is the Guild object where the reaction occurred
    moderator_role = discord.utils.find(lambda r: r.name == "MODERATOR", guild.roles)
    return moderator_role in user.roles if moderator_role else False


@bot.slash_command(guild_ids=[690232443718074471], name='removedeckfromdatabase', description='Remove a deck from the database. Only accessible to moderators.')
@option("old_deck", autocomplete=deck_autocomplete)
@option("new_deck", autocomplete=deck_autocomplete)
async def remove_deck(ctx, old_deck: str, new_deck: str = None):
    user = ctx.author  # Get the user who issued the command
    guild = ctx.guild  # Get the guild (server) where the command was issued

    # Check if the user has the "MODERATOR" role
    if not user_has_permission(user, guild):
        embed = discord.Embed(
            title="Permission Denied",
            description="You do not have permission to use this command.",
            color=0xff0000
        )
        await ctx.respond(embed=embed, ephemeral=True)  # Send an ephemeral message only visible to the user
        logger.warning("User %s attempted to use /removedeck without sufficient permissions.", user.name)
        return

    # Format and check the deck names
    old_deck_to_remove = format_deck_name(old_deck)
    display_old_deck_name = capitalize_words(old_deck_to_remove)  # Format the old deck name for display

    # Log the attempt to remove a deck
    logger.info("Attempting to remove a deck: %s", old_deck_to_remove)

    # Check if the old deck exists
    old_deck_entry = await decks_on_database.find_one({"name": old_deck_to_remove})
    if not old_deck_entry:
        embed = discord.Embed(
            title="Deck Not Found",
            description=f"{display_old_deck_name} does not exist in the database.",
            color=0xff0000
        )
        logger.warning("Attempted to remove a deck that does not exist: %s", old_deck_to_remove)
        await ctx.respond(embed=embed)
        return
    
    # Check for associated data if no new deck is provided
    if not new_deck:
        # Check if there are any individual results associated with the old deck
        associated_results = await individual_results.find_one({"deck_name": old_deck_to_remove})
        
        # Check if there are player entries in the old deck
        associated_players = old_deck_entry.get("players", [])

        if associated_results or associated_players:
            # If there are associated results or players, prevent removal
            embed = discord.Embed(
                title="Cannot Remove Deck",
                description=(
                    f"{display_old_deck_name} has associated game logs or player data. \n"
                    "Specify a deck to transfer these records before removal."
                ),
                color=0xff0000
            )
            logger.warning("Attempted to remove deck %s with associated data without specifying a transfer.", old_deck_to_remove)
            await ctx.respond(embed=embed)
            return

    # If a new deck name is provided, transfer logs to it
    if new_deck:
        new_deck_name = format_deck_name(new_deck)
        display_new_deck_name = capitalize_words(new_deck_name)  # Format the new deck name for display

        # Check if the new deck exists
        new_deck_entry = await decks_on_database.find_one({"name": new_deck_name})
        if not new_deck_entry:
            embed = discord.Embed(
                title="New Deck Not Found",
                description=f"{display_new_deck_name} does not exist in the database. Cannot transfer logs.",
                color=0xff0000
            )
            logger.warning("New deck %s does not exist; cannot transfer logs from %s", new_deck_name, old_deck_to_remove)
            await ctx.respond(embed=embed)
            return

        # Update game logs to use the new deck name
        update_result = await individual_results.update_many(
            {"deck_name": old_deck_to_remove},
            {"$set": {"deck_name": new_deck_name}}
        )

        # Log the update operation
        logger.info("Updated %d game logs from %s to %s", update_result.modified_count, old_deck_to_remove, new_deck_name)


        # Initialize players list in new deck entry if it doesn't exist
        if "players" not in new_deck_entry:
            new_deck_entry["players"] = []

        # Transfer player data from the old deck to the new deck
        for old_player in old_deck_entry.get("players", []):
            old_player_id = old_player["player_id"]
            old_player_stats = {
                "wins": old_player["wins"],
                "losses": old_player["losses"],
                "draws": old_player["draws"]
            }

            # Check if the player already exists in the new deck
            player_exists = False
            for new_player in new_deck_entry["players"]:
                if new_player["player_id"] == old_player_id:
                    # Update existing player stats
                    new_player["wins"] += old_player_stats["wins"]
                    new_player["losses"] += old_player_stats["losses"]
                    new_player["draws"] += old_player_stats["draws"]
                    player_exists = True
                    break

            if not player_exists:
                # Add the player from the old deck to the new deck
                new_deck_entry["players"].append({
                    "player_id": old_player_id,
                    "wins": old_player_stats["wins"],
                    "losses": old_player_stats["losses"],
                    "draws": old_player_stats["draws"]
                })

        # Update the new deck entry with the transferred player data
        await decks_on_database.update_one(
            {"name": new_deck_name},
            {"$set": {"players": new_deck_entry["players"]}}
        )
        
        # Update game logs in matches to use the new deck name
        matches_update_result = await matches.update_many(
            {"players.deck_name": old_deck_to_remove},
            {"$set": {"players.$[elem].deck_name": new_deck_name}},
            array_filters=[{"elem.deck_name": old_deck_to_remove}]
        )
        
        # Log the update operation for matches
        logger.info("Updated %d match logs from %s to %s in matches", matches_update_result.modified_count, old_deck_to_remove, new_deck_name)

    # Remove the old deck from the database
    await decks_on_database.delete_one({"name": old_deck_to_remove})

    # Create the response message
    if new_deck:
        embed = discord.Embed(
            title="Deck Removed and Logs Updated",
            description=f"{display_old_deck_name} was removed and all associated game logs were transferred to {display_new_deck_name}. Player stats have been updated accordingly.",
            color=0x00ff00
        )
        logger.info("Successfully removed deck %s and transferred logs and player data to %s", old_deck_to_remove, new_deck_name)
    else:
        embed = discord.Embed(
            title="Deck Removed",
            description=f"{display_old_deck_name} was successfully removed from the database.",
            color=0x00ff00
        )
        logger.info("Successfully removed deck from the database: %s", old_deck_to_remove)

    await ctx.respond(embed=embed)

async def delete_track_data(game_id):
    print(f"Starting to delete Game: {game_id}")
    # Convert game_id to the appropriate type if necessary
    game_id = int(game_id)

    # Delete the match entry
    await matches.delete_one({"match_id": game_id})

    # Fetch individual results to reverse the statistics updates
    individual_results_cursor = individual_results.find({"match_id": game_id})
    async for result in individual_results_cursor:
        player_id = result['player_id']
        deck_name = result['deck_name']
        outcome = result['result']

        # Reverse the win/loss/draw count in the decks collection
        update_field = ""
        if outcome == "win":
            update_field = 'players.$.wins'
        elif outcome == "loss":
            update_field = 'players.$.losses'
        elif outcome == "draw":
            update_field = 'players.$.draws'

        if update_field:
            await decks_on_database.update_one(
                {'name': deck_name, 'players.player_id': player_id},
                {'$inc': {update_field: -1}}
            )
        
        # Check if the player's wins, losses, and draws are all zero and remove the player if true
        deck_document = await decks_on_database.find_one({'name': deck_name, 'players.player_id': player_id}, {'players.$': 1})
        player_stats = deck_document['players'][0]  # Assumes that 'players' is an array and we've found the correct subdocument
        if all(stat == 0 for stat in [player_stats.get('wins', 0), player_stats.get('losses', 0), player_stats.get('draws', 0)]):
            await decks_on_database.update_one(
                {'name': deck_name},
                {'$pull': {'players': {'player_id': player_id}}}
            )

    # Delete individual results for the match
    await individual_results.delete_many({"match_id": game_id})
    
    # Fetch all games with a match_id greater than the deleted game's match_id
    async for subsequent_game in matches.find({"match_id": {"$gt": game_id}}).sort("match_id", 1):
        # Decrease match_id by 1 for each subsequent game
        new_match_id = subsequent_game["match_id"] - 1
        await matches.update_one({"_id": subsequent_game["_id"]}, {"$set": {"match_id": new_match_id}})
        await individual_results.update_many({"match_id": subsequent_game["match_id"]}, {"$set": {"match_id": new_match_id}})
    
    # Decrement the match_id counter to maintain sequence integrity
    await db.counters.update_one({"_id": "match_id"}, {"$inc": {"sequence_value": -1}})



@bot.slash_command(guild_ids=[690232443718074471], name='findmisnameddecks', description='Find and suggest corrections for misnamed decks in logs. (Mods only)')
async def find_misnamed_decks(ctx):
    user = ctx.author  # Get the user who issued the command
    guild = ctx.guild  # Get the guild (server) where the command was issued

    # Check if the user has the "MODERATOR" role
    if not user_has_permission(user, guild):
        embed = discord.Embed(
            title="Permission Denied",
            description="You do not have permission to use this command.",
            color=0xff0000
        )
        await ctx.respond(embed=embed, ephemeral=True)  # Send an ephemeral message only visible to the user
        logger.warning("User %s attempted to use /findmisnameddecks without sufficient permissions.", user.name)
        return

    # Fetch all decks from the "decks" collection
    existing_decks_cursor = decks_on_database.find()
    existing_decks = [deck['name'] async for deck in existing_decks_cursor]

    # Fetch all deck names from "individual_results" and "matches"
    individual_results_cursor = await individual_results.distinct("deck_name")
    matches_cursor = await matches.distinct("players.deck_name")

    logged_decks = set(individual_results_cursor + matches_cursor)

    # Identify decks in logs that are not in the "decks" collection
    missing_decks = list(logged_decks - set(existing_decks))

    # Prepare a response with suggestions for each missing deck
    embed = discord.Embed(title="Misnamed Decks Found", color=0x00ff00)
    suggestions = {}
    
    for missing_deck in missing_decks:
        # Find the closest matches using multiple scoring methods
        # Use token set ratio to allow for partial matches with reordering
        closest_matches = process.extract(
            missing_deck, 
            existing_decks, 
            scorer=fuzz.token_set_ratio, 
            limit=5
        )

        # Consider matches with a score above 60 to account for minor differences
        # Use multiple scoring strategies to refine suggestions
        refined_matches = [
            match for match in closest_matches 
            if match[1] >= 60 or fuzz.partial_ratio(missing_deck, match[0]) >= 60
        ]

        suggestions[missing_deck] = [match[0] for match in refined_matches]

        if suggestions[missing_deck]:
            suggestion_text = ', '.join(suggestions[missing_deck])
            embed.add_field(name=missing_deck, value=suggestion_text, inline=False)
        else:
            embed.add_field(name=missing_deck, value="No close matches found", inline=False)

    # If no missing decks are found
    if not suggestions:
        embed = discord.Embed(
            title="No Misnamed Decks Found",
            description="All logged decks exist in the database.",
            color=0x00ff00
        )
    else:
        logger.info("Found %d misnamed decks with suggestions", len(suggestions))

    await ctx.respond(embed=embed)


@bot.slash_command(guild_ids=[690232443718074471], name='correctmisnameddeck', description='Correct misnamed decks in the logs and update stats. (Mods only)')
@option("misnamed_deck", description="The misnamed deck to correct", autocomplete=misnamed_deck_autocomplete)
@option("correct_deck", description="The correct deck name", autocomplete=deck_autocomplete)
async def correct_misnamed_decks(ctx, misnamed_deck: str, correct_deck: str):
    user = ctx.author  # Get the user who issued the command
    guild = ctx.guild  # Get the guild (server) where the command was issued

    # Check if the user has the "MODERATOR" role
    if not user_has_permission(user, guild):
        embed = discord.Embed(
            title="Permission Denied",
            description="You do not have permission to use this command.",
            color=0xff0000
        )
        await ctx.respond(embed=embed, ephemeral=True)  # Send an ephemeral message only visible to the user
        logger.warning("User %s attempted to use /correctmisnameddecks without sufficient permissions.", user.name)
        return
    
    # Fetch all valid deck names from the database
    valid_decks = await decks_on_database.distinct("name")
    
    # Validate the misnamed_deck to ensure it is not a valid deck
    if misnamed_deck in valid_decks:
        embed = discord.Embed(
            title="Invalid Misnamed Deck",
            description=f"The deck '{misnamed_deck}' is a valid deck name. Please ensure you selected the correct misnamed deck.",
            color=0xff0000
        )
        await ctx.respond(embed=embed, ephemeral=True)
        return

    # Validate the selected correct_deck against the list of valid decks
    if correct_deck not in valid_decks:
        embed = discord.Embed(
            title="Invalid Deck Name",
            description=f"The deck '{correct_deck}' is not a valid deck name. Please choose from the autocomplete options.",
            color=0xff0000
        )
        await ctx.respond(embed=embed, ephemeral=True)
        return
    

    # Check if the correct deck exists in the database
    correct_deck_entry = await decks_on_database.find_one({"name": correct_deck})
    if not correct_deck_entry:
        embed = discord.Embed(
            title="Correct Deck Not Found",
            description=f"{correct_deck} does not exist in the database. Cannot proceed with correction.",
            color=0xff0000
        )
        logger.warning("Correct deck %s does not exist; cannot correct misnamed deck %s", correct_deck, misnamed_deck)
        await ctx.respond(embed=embed)
        return

    # Step 1: Update individual_results collection
    update_individual_results = await individual_results.update_many(
        {"deck_name": misnamed_deck},
        {"$set": {"deck_name": correct_deck}}
    )
    
    # Check if any individual records were updated
    if update_individual_results.modified_count == 0:
        print(f"No records were updated in individual_results for deck_name '{misnamed_deck}'.")
    else:
        print(f"Updated {update_individual_results.modified_count} records in individual_results.")


    # Step 2: Update matches collection
    update_matches = await matches.update_many(
        {"players.deck_name": misnamed_deck},
        {"$set": {"players.$[elem].deck_name": correct_deck}},
        array_filters=[{"elem.deck_name": misnamed_deck}]
    )

    # Check if any match records were updated
    if update_matches.modified_count == 0:
        print(f"No records were updated in matches for deck_name '{misnamed_deck}'.")
    else:
        print(f"Updated {update_matches.modified_count} records in matches.")
    
    # Step 3: Recalculate stats for the correct deck
    # Initialize stats
    player_stats = {}

    # Fetch all updated results for the correct deck
    async for result in individual_results.find({"deck_name": correct_deck}):
        player_id = str(result["player_id"])
        result_type = result["result"]

        if player_id not in player_stats:
            player_stats[player_id] = {"wins": 0, "losses": 0, "draws": 0}

        if result_type == "win":
            player_stats[player_id]["wins"] += 1
        elif result_type == "loss":
            player_stats[player_id]["losses"] += 1
        elif result_type == "draw":
            player_stats[player_id]["draws"] += 1

    # Update the correct deck entry with the new stats
    correct_deck_entry["players"] = [
        {"player_id": player_id, **stats}
        for player_id, stats in player_stats.items()
    ]
    await decks_on_database.update_one(
        {"name": correct_deck},
        {"$set": {"players": correct_deck_entry["players"]}}
    )

    # Create the response message
    embed = discord.Embed(
        title="Deck Correction Successful",
        description=f"All instances of '{misnamed_deck}' have been corrected to '{correct_deck}'. "
                    f"Updated {update_individual_results.modified_count} records in individual results and "
                    f"{update_matches.modified_count} records in match logs. Deck stats have been updated.",
        color=0x00ff00
    )

    await ctx.respond(embed=embed)
    
@bot.slash_command(guild_ids=[690232443718074471], name='editdeckindatabase', description='Edit the name of a deck across all instances in the database. (Mods only)')
@option("old_deck_name", description="The current deck name to edit", autocomplete=deck_autocomplete)
@option("new_deck_name", description="The new deck name")
async def edit_deck_name(ctx, old_deck_name: str, new_deck_name: str):
    user = ctx.author
    guild = ctx.guild

    # Check if the user has the "MODERATOR" role
    if not user_has_permission(user, guild):
        embed = discord.Embed(
            title="Permission Denied",
            description="You do not have permission to use this command.",
            color=0xff0000
        )
        await ctx.respond(embed=embed, ephemeral=True)
        return

    # Check if the new deck name already exists
    existing_deck = await decks_on_database.find_one({"name": new_deck_name})
    if existing_deck:
        embed = discord.Embed(
            title="Deck Name Already Exists",
            description=f"The deck name '{new_deck_name}' is already in use. Please choose a different name.",
            color=0xff0000
        )
        await ctx.respond(embed=embed, ephemeral=True)
        return

    # Step 1: Update the deck name in the `decks` collection
    update_decks = await decks_on_database.update_one(
        {"name": old_deck_name},
        {"$set": {"name": new_deck_name}}
    )

    if update_decks.matched_count == 0:
        embed = discord.Embed(
            title="Deck Not Found",
            description=f"The deck '{old_deck_name}' was not found in the database.",
            color=0xff0000
        )
        await ctx.respond(embed=embed, ephemeral=True)
        return

    # Step 2: Update the deck name in the `individual_results` collection
    update_individual_results = await individual_results.update_many(
        {"deck_name": old_deck_name},
        {"$set": {"deck_name": new_deck_name}}
    )

    # Step 3: Update the deck name in the `matches` collection
    update_matches = await matches.update_many(
        {"players.deck_name": old_deck_name},
        {"$set": {"players.$[elem].deck_name": new_deck_name}},
        array_filters=[{"elem.deck_name": old_deck_name}]
    )

    # Step 4: Confirm the changes to the user
    embed = discord.Embed(
        title="Database Deck Name Updated",
        description=f"'{old_deck_name}' has been successfully renamed to '{new_deck_name}'. "
                    f"Updated {update_decks.modified_count} deck entry, {update_individual_results.modified_count} records in individual results, "
                    f"and {update_matches.modified_count} records in matches.",
        color=0x00ff00
    )

    await ctx.respond(embed=embed)
    
def format_match_log(match):
    # General match information
    match_details = [
        f"Match ID: {match.get('match_id', 'Unknown')}",
        f"Date: {match.get('date', 'Unknown')}"
    ]
        # Retrieve each player's details
    player_details = []
    for i, player in enumerate(match.get("players", []), start=1):
        player_id = player.get('player_id', 'Unknown')
        player_info = (
            f"**Player {i}:**",
            f"  Player ID: {player_id} (<@{player_id}>)",  # Adding the mention here
            f"  Deck: {player.get('deck_name', 'Unknown')}",
            f"  Result: {player.get('result', 'Unknown')}",
            f"  Seat: {player.get('position', 'Unknown')}"
        )
        player_details.extend(player_info)
    
    # Combine match and player details
    match_log = "\n".join(match_details + player_details)
    
    return match_log



async def get_top_decks(player_id):
    pipeline = [
        {"$match": {"player_id": player_id}},  # Filter documents by player_id
        {"$group": {"_id": "$deck_name", "count": {"$sum": 1}}},  # Count occurrences of each deck
        {"$sort": {"count": DESCENDING}},  # Sort by count in descending order
        {"$limit": 5}  # Limit to top 5 decks
    ]
    results = await individual_results.aggregate(pipeline).to_list(length=5)
    return [doc["_id"] for doc in results]  # Return only deck names


    
@bot.slash_command(name="edittrack", description="Edit a tracked match result by match ID. (Mods only)")
@commands.has_permissions(manage_messages=True)  # Ensure only moderators can use this
@option("match_id", description="The ID of the match to edit")
async def edittrack(ctx: Interaction, match_id: str):
    await ctx.defer()
    
    # Convert match_id to integer to match database storage type
    try:
        match_id = int(match_id)
    except ValueError:
        await ctx.response.send_message("Invalid match ID format. Please enter a numeric match ID.",
                                        # ephemeral=True
                                        )
        return

    # Step 1: Retrieve the match by match_id
    match = await matches.find_one({"match_id": match_id})
    if not match:
        # Use follow-up for the response since interaction was deferred
        await ctx.followup.send("Match not found. Please verify the match ID and try again.",
                                # ephemeral=True
                                )
        return

    # Step 2: Display match details for confirmation
    match_log = format_match_log(match)  # Function to format match details into readable text
    await ctx.followup.send(
        content=f"{match_log}\n\nIs this the correct game to edit?",
        # ephemeral=True,
        view=ConfirmView(ctx, match, match_id)  # Create a confirmation view with Yes/No buttons
    )

class ConfirmView(discord.ui.View):
    def __init__(self, ctx, match, match_id):
        super().__init__()
        self.ctx = ctx
        self.match = match
        self.match_id = match_id
        self.add_item(CancelButton())  # Add the cancel button

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
    async def confirm(self, button: discord.ui.Button, interaction: Interaction):
        # Proceed to select player
        await interaction.response.send_message(
            content="Which player would you like to edit? (1, 2, 3, or 4)",
            # ephemeral=True,
            view=PlayerSelectView(self.ctx, self.match, self.match_id)
        )
        
class PlayerDropdown(discord.ui.Select):
    def __init__(self, ctx, match, match_id, selected_player):
        self.ctx = ctx
        self.match = match
        self.match_id = match_id
        self.selected_player = selected_player

        options = [
            discord.SelectOption(label=member.display_name, value=str(member.id))
            for member in ctx.guild.members
            if not member.bot
        ]

        super().__init__(
            placeholder="Select a new player for this seat...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        new_player_id = int(self.values[0])
        player_index = self.selected_player - 1
        old_player = self.match["players"][player_index]

        # Update the match document
        await matches.update_one(
            {"match_id": self.match_id},
            {"$set": {f"players.{player_index}.player_id": new_player_id}}
        )

        # Update the individual_results record: remove old, add new
        await individual_results.delete_one({
            "match_id": self.match_id,
            "player_id": old_player["player_id"]
        })

        await individual_results.insert_one({
            "match_id": self.match_id,
            "player_id": new_player_id,
            "deck_name": old_player["deck_name"],
            "seat": old_player["position"],
            "result": old_player["result"],
            "date": self.match.get("date", datetime.utcnow())
        })

        await interaction.response.send_message(
            f"✅ Player for seat {self.selected_player} has been updated to <@{new_player_id}>.",
            ephemeral=True
        )
        
class PlayerSelectionView(discord.ui.View):
    def __init__(self, ctx, match, match_id, selected_player):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.match = match
        self.match_id = match_id
        self.selected_player = selected_player

        self.add_item(PlayerDropdown(ctx, match, match_id, selected_player))
        self.add_item(CancelButton())
        
class EditChoiceView(discord.ui.View):
    def __init__(self, ctx, match, match_id, selected_player):
        super().__init__()
        self.ctx = ctx
        self.match = match
        self.match_id = match_id
        self.selected_player = selected_player
        self.add_item(CancelButton())

    @discord.ui.button(label="Edit Deck", style=discord.ButtonStyle.primary)
    async def edit_deck(self, button, interaction):
        deck_view = await DeckInputView.create(self.ctx, self.match, self.match_id, self.selected_player)
        await interaction.response.send_message("Select a new deck or input a custom one.", view=deck_view)

    @discord.ui.button(label="Change Player", style=discord.ButtonStyle.secondary)
    async def change_player(self, button: discord.ui.Button, interaction: discord.Interaction):
        view = PlayerSelectionView(self.ctx, self.match, self.match_id, self.selected_player)
        await interaction.response.send_message("Select a new player for this seat:", view=view)



class PlayerSelectView(discord.ui.View):
    def __init__(self, ctx, match, match_id):
        super().__init__()
        self.ctx = ctx
        self.match = match
        self.match_id = match_id
        self.add_item(CancelButton())

    @discord.ui.select(
        placeholder="Select a player to edit...",
        options=[discord.SelectOption(label=f"Player {i+1}", value=str(i+1)) for i in range(4)]
    )
    async def select_callback(self, select, interaction: Interaction):
        selected_player = int(select.values[0])
        await interaction.response.send_message(
            content="What would you like to edit?",
            view=EditChoiceView(self.ctx, self.match, self.match_id, selected_player)
        )

class DeckInputView(discord.ui.View):
    @staticmethod
    async def create(ctx, match, match_id, selected_player):
        view = DeckInputView(ctx, match, match_id, selected_player)
        await view.setup_deck_buttons()  # Ensure setup_deck_buttons runs fully
        view.add_item(CancelButton())  # Add the Cancel button last
        return view
    
    def __init__(self, ctx, match, match_id, selected_player):
        super().__init__()
        self.ctx = ctx
        self.match = match
        self.match_id = match_id
        self.selected_player = selected_player
        # Retrieve player_id from player at selected position
        self.player_id = self.get_player_id_by_position(selected_player)
        
    def get_player_id_by_position(self, position):
        # Find the player with the matching position
        for player in self.match["players"]:
            if player["position"] == position:
                return player["player_id"]
        return None  # Return None if no player found for that position

    async def setup_deck_buttons(self):
        # Fetch the top 5 most-used decks for this player
        top_decks = await get_top_decks(self.player_id)  # Use player_id to get the most-used decks
        print("Top decks for player:", top_decks)  # Debugging statement
        # Add a button for each top deck
        for deck_name in top_decks:
            self.add_item(DeckButton(deck_name, self))

        # Add a button for custom entry
        self.add_item(CustomDeckInputButton(self))

    async def update_selected_deck(self, deck_name, interaction: discord.Interaction):
        await interaction.response.send_message(
            content=f"The selected deck has been updated to: {deck_name}",
            # ephemeral=True
        )
        
        
class CancelButton(discord.ui.Button):
    def __init__(self, label="Cancel"):
        super().__init__(label=label, style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Process has been canceled.", view=None)  # Remove buttons and disable interaction
        self.view.stop()  # Stop the view to prevent further interaction


class FinalConfirmationView(discord.ui.View):
    def __init__(self, ctx, match, match_id, selected_player, old_deck, new_deck):
        super().__init__()
        self.ctx = ctx
        self.match = match
        self.match_id = match_id
        self.selected_player = selected_player
        self.old_deck = old_deck
        self.new_deck = new_deck
        self.player_id = self.get_player_id_by_position(selected_player)
        self.add_item(CancelButton())  # Add a cancel button
        
    def get_player_id_by_position(self, position):
        # Find the player with the matching position
        for player in self.match["players"]:
            if player["position"] == position:
                return player["player_id"]
        return None  # Return None if no player found for that position

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
        # Proceed with the deck update in the database
        await update_deck_collection(self.match["players"][self.selected_player - 1]["player_id"],
                                     self.old_deck, self.new_deck, 
                                     self.match["players"][self.selected_player - 1]["result"])

        await matches.update_one(
            {"match_id": self.match_id},
            {"$set": {f"players.{self.selected_player - 1}.deck_name": self.new_deck}}
        )
        
        await individual_results.update_one(
                {"match_id": self.match_id, "player_id": self.match["players"][self.selected_player - 1]["player_id"]},
                {"$set": {"deck_name": self.new_deck}}
            )

        await interaction.response.send_message(
            content=f"Deck updated from **{self.old_deck}** to **{self.new_deck}** successfully.",
            # ephemeral=True
        )
        self.stop()


class DeckButton(discord.ui.Button):
    def __init__(self, label, parent_view):
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        # Show final confirmation before updating
        old_deck = self.parent_view.match["players"][self.parent_view.selected_player - 1]["deck_name"]
        new_deck = self.label

        await interaction.response.send_message(
            content=f"Confirm deck change from **{old_deck}** to **{new_deck}**?",
            view=FinalConfirmationView(
                self.parent_view.ctx, self.parent_view.match, self.parent_view.match_id,
                self.parent_view.selected_player, old_deck, new_deck
            )
        )


class CustomDeckInputButton(discord.ui.Button):
    def __init__(self, parent_view):
        super().__init__(label="Enter Custom Deck", style=discord.ButtonStyle.secondary)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        new_deck = self.children[1].value
        player = self.match["players"][self.selected_player - 1]
        old_deck = player["deck_name"]

        # Show final confirmation
        await interaction.response.send_message(
            content=f"Confirm deck change from **{old_deck}** to **{new_deck}**?",
            view=FinalConfirmationView(self.ctx, self.match, self.match_id, self.selected_player, old_deck, new_deck)
        )


class CustomDeckInputModal(discord.ui.Modal):
    def __init__(self, ctx, match, match_id, selected_player):
        super().__init__(title="Enter Custom Deck Name")
        self.ctx = ctx
        self.match = match
        self.match_id = match_id
        self.selected_player = selected_player

        # Add a warning label to ensure correct input format
        self.add_item(
            discord.ui.InputText(
                label="WARNING",
                value="Please match the exact deck name with correct case as in the database.",
                style=discord.TextStyle.short,
                required=False
            )
        )
        
        # Add input field for the custom deck name
        self.add_item(
            discord.ui.InputText(
                label="Custom Deck Name",
                placeholder="Type the deck name here"
            )
        )
            
    async def callback(self, interaction: discord.Interaction):
        try:
            new_deck = self.children[0].value  # The custom deck name entered
            # Adjust for 0-based indexing in the players array
            player = self.match["players"][self.selected_player - 1]
            player_id = player["player_id"]  # Correct player ID
            old_deck = player["deck_name"]  # Old deck name
            result = player["result"]  # Result for the player

            # Update collections with the new deck name
            await update_deck_collection(player_id, old_deck, new_deck, result)
            await individual_results.update_one(
                {"match_id": self.match_id, "player_id": player_id},
                {"$set": {"deck_name": new_deck}}  # Update the deck name in the individual results
            )
            await matches.update_one(
                {"match_id": self.match_id},
                {"$set": {f"players.{self.selected_player - 1}.deck_name": new_deck}}  # Update the match with the new deck
            )
            await interaction.response.send_message("The deck has been updated successfully.")
        except Exception as e:
            await interaction.response.send_message(f"Failed to update the deck: {e}")
        


async def update_deck_collection(player_id, old_deck, new_deck, result):
    # Determine the field to increment or decrement based on the result
    result_field = "wins" if result == "win" else "losses" if result == "loss" else "draws"

    # Remove the result from the old deck for the specified player
    await decks_on_database.update_one(
        {"name": old_deck, "players.player_id": player_id},
        {"$inc": {f"players.$.{result_field}": -1}}
    )

    # Remove the player entry if all results (wins, losses, draws) are now zero
    old_player_stats = await decks_on_database.find_one(
        {"name": old_deck, "players.player_id": player_id},
        {"players.$": 1}
    )
    if old_player_stats:
        player_stats = old_player_stats["players"][0]
        if player_stats["wins"] == 0 and player_stats["losses"] == 0 and player_stats["draws"] == 0:
            await decks_on_database.update_one(
                {"name": old_deck},
                {"$pull": {"players": {"player_id": player_id}}}
            )

    # Add the result to the new deck for the specified player, creating an entry if necessary
    await decks_on_database.update_one(
        {"name": new_deck, "players.player_id": player_id},
        {"$inc": {f"players.$.{result_field}": 1}},
        upsert=False  # Only update if an entry already exists
    )

    # If no player entry was found for the new deck, add a new player record
    player_entry_exists = await decks_on_database.find_one(
        {"name": new_deck, "players.player_id": player_id}
    )
    if not player_entry_exists:
        await decks_on_database.update_one(
            {"name": new_deck},
            {"$push": {"players": {"player_id": player_id, "wins": 1 if result == "win" else 0,
                                   "losses": 1 if result == "loss" else 0,
                                   "draws": 1 if result == "draw" else 0}}}
        )
    
async def fetch_general_stats():
    postban_start_date = datetime(2024, 9, 24, tzinfo=timezone.utc)

    async def get_win_stats(match_criteria):
        total_games_per_seat = {}
        wins_by_seat = {}

        # Count the number of games per seat for the given match criteria
        total_individual_results = 0

        for seat in range(1, 5):  # Assuming seats 1 through 4
            count = await individual_results.count_documents({**match_criteria, "seat": seat})
            total_games_per_seat[seat] = count
            total_individual_results += count

        # Calculate wins per seat
        wins_cursor = individual_results.aggregate([
            {"$match": {**match_criteria, "result": "win"}},
            {"$group": {"_id": "$seat", "wins": {"$sum": 1}}},
            {"$sort": {"_id": 1}}
        ])
        async for doc in wins_cursor:
            wins_by_seat[doc["_id"]] = doc["wins"]

        # Calculate win percentage per seat
        win_percentage_by_seat = {}
        for seat in range(1, 5):
            total_games = total_games_per_seat.get(seat, 0)
            wins = wins_by_seat.get(seat, 0)
            win_percentage = (wins / total_games) * 100 if total_games > 0 else 0
            win_percentage_by_seat[seat] = round(win_percentage)

        # Calculate the total number of games (every 4 individual results make up a game)
        total_games = total_individual_results // 4
        
        return win_percentage_by_seat, total_games

    # Pre-ban stats
    preban_stats, preban_total_games = await get_win_stats({"date": {"$lt": postban_start_date}})

    # Post-ban stats
    postban_stats, postban_total_games = await get_win_stats({"date": {"$gte": postban_start_date}})

    return {
        "preban_stats": preban_stats,
        "preban_total_games": preban_total_games,
        "postban_stats": postban_stats,
        "postban_total_games": postban_total_games,
    }


def create_general_stats_embed(stats):
    preban_stats = stats["preban_stats"]
    postban_stats = stats["postban_stats"]
    preban_total_games = stats["preban_total_games"]
    postban_total_games = stats["postban_total_games"]

    def format_stats(stats_by_seat):
        total_sum = sum(stats_by_seat.values())
        field_value = ""
        for seat in range(1, 5):
            win_percentage = stats_by_seat.get(seat, 0)
            suffix = "th"
            if seat == 1:
                suffix = "st"
            elif seat == 2:
                suffix = "nd"
            elif seat == 3:
                suffix = "rd"
            field_value += f"{seat}{suffix}: {win_percentage}%\n"
        
        # Calculate and append draw percentage
        draw_percentage = max(0, 100 - total_sum)
        field_value += f"**Draw**: {int(draw_percentage)}%\n"
        return field_value

    # Embed creation
    embed = discord.Embed(title="General Stats", color=0x00ff00)

    # Pre-ban stats
    preban_field_value = format_stats(preban_stats)
    embed.add_field(name=f"Global Win Percentage by Seat (PRE-BAN) ({preban_total_games} Games)", value=preban_field_value, inline=False)

    # Post-ban stats
    postban_field_value = format_stats(postban_stats)
    embed.add_field(name=f"Global Win Percentage by Seat (POST-BAN) ({postban_total_games} Games)", value=postban_field_value, inline=False)

    return embed




@bot.slash_command(name="generalstats", description="Display general statistics.")
# @ephemeral_in_private_channel
async def generalstats(ctx):
    ephemeral = ctx.channel.id == 1299454152140918848

    # Fetch stats from the database
    stats = await fetch_general_stats()
    # Create and send the embed
    embed = create_general_stats_embed(stats)
    await ctx.respond(embed=embed, ephemeral=ephemeral)
    


event_registrations = db.event_registrations 

@bot.slash_command(guild_ids=[guild_id], name="events", description="View and register for current events.")
async def events(ctx: discord.ApplicationContext):
    guild_events = await ctx.guild.fetch_scheduled_events()

    if not guild_events:
        await ctx.respond("There are no scheduled events.", ephemeral=True)
        return

    current_event = guild_events[0]

    # Count registrations
    registration_count = await event_registrations.count_documents({"event_id": str(current_event.id)})
    event_time_str = f"<t:{int(current_event.start_time.timestamp())}:F>"

    is_mod = ctx.author.guild_permissions.manage_messages

    # Initial short embed
    short_embed = discord.Embed(
        title="🏆 Scheduled Events 🏆",
        description="There's 1 scheduled event.",
        color=0x00BFFF
    )
    short_embed.add_field(name="Name", value=current_event.name, inline=False)
    short_embed.add_field(name="Start Date", value=event_time_str, inline=False)
    short_embed.add_field(name="Registered", value=f"{registration_count} participants", inline=False)

    class InitialView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            self.add_item(OpenDetailsButton())
            if is_mod:
                self.add_item(SeeParticipantsButton())

    class OpenDetailsButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Would you like to register for the event?", style=discord.ButtonStyle.primary)

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("This is not your interaction.", ephemeral=True)
                return

            now = datetime.now(timezone.utc)
            time_diff = current_event.start_time - now
            registration_open = time_diff.total_seconds() > 900

            # Check if user is already registered
            existing = await event_registrations.find_one({
                "event_id": str(current_event.id),
                "user_id": str(interaction.user.id)
            })

            # Prepare embed
            registration_count = await event_registrations.count_documents({"event_id": str(current_event.id)})
            event_time_str = f"<t:{int(current_event.start_time.timestamp())}:F>"

            detailed_embed = discord.Embed(
                title=current_event.name,
                description=current_event.description or "No description provided.",
                color=0x00ff00
            )
            if current_event.cover:
                detailed_embed.set_image(url=current_event.cover)

            detailed_embed.add_field(name="Start Time", value=event_time_str, inline=False)
            detailed_embed.add_field(name="Registered Participants", value=str(registration_count), inline=False)

            # Build the dynamic view
            view = discord.ui.View()

            if registration_open:
                if existing:
                    view.add_item(AlreadyRegisteredButton())
                    view.add_item(UnregisterButton())
                else:
                    view.add_item(RegisterButton())
            else:
                view.add_item(discord.ui.Button(label="Registration Closed", style=discord.ButtonStyle.secondary, disabled=True))


            await interaction.response.edit_message(embed=detailed_embed, view=view)


    class RegisterButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Register", style=discord.ButtonStyle.success)

        async def callback(self, interaction: discord.Interaction):
            now = datetime.now(timezone.utc)
            time_diff = current_event.start_time - now

            if time_diff.total_seconds() <= 600:
                await interaction.response.send_message("❌ Registration is closed (10 minutes before event start).", ephemeral=True)
                return

            # Check if already registered
            existing = await event_registrations.find_one({
                "event_id": str(current_event.id),
                "user_id": str(interaction.user.id)
            })

            if existing:
                await interaction.response.send_message(
                    f"⚠️ You’re already registered for the event: **{current_event.name}**.",
                    ephemeral=True
                )
                return

            # Register the user
            await event_registrations.update_one(
                {"event_id": str(current_event.id), "user_id": str(interaction.user.id)},
                {"$set": {"timestamp": datetime.now(timezone.utc)}},
                upsert=True
            )

            # Optionally: Disable the button visually after registration
            self.disabled = True
            await interaction.response.edit_message(view=self.view)

            await interaction.followup.send(
                f"✅ You have been registered for the event: **{current_event.name}**.",
                ephemeral=True
            )


    class SeeParticipantsButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Participant Details (MOD ONLY)", style=discord.ButtonStyle.secondary)

        async def callback(self, interaction: discord.Interaction):
            if not interaction.user.guild_permissions.manage_messages:
                await interaction.response.send_message("You don’t have permission to view this.", ephemeral=True)
                return

            participants = await event_registrations.find({"event_id": str(current_event.id)}).to_list(length=100)

            if not participants:
                await interaction.response.send_message("No participants registered yet.", ephemeral=True)
                return

            embed = discord.Embed(
                title="Participant Details",
                color=0xaaaaaa
            )

            for p in participants:
                user = ctx.guild.get_member(int(p["user_id"]))
                username = user.display_name if user else f"User ID {p['user_id']}"
                timestamp = p.get("timestamp")
                timestamp_str = f"<t:{int(timestamp.timestamp())}:R>" if timestamp else "Unknown"
                embed.add_field(name=username, value=f"Registered {timestamp_str}", inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)
            
    class AlreadyRegisteredButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="✅ Already Registered", style=discord.ButtonStyle.success, disabled=True)


    class UnregisterButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="❌ Unregister", style=discord.ButtonStyle.danger)

        async def callback(self, interaction: discord.Interaction):
            result = await event_registrations.delete_one({
                "event_id": str(current_event.id),
                "user_id": str(interaction.user.id)
            })

            if result.deleted_count > 0:
                await interaction.response.send_message(f"❌ You have been unregistered from **{current_event.name}**.", ephemeral=True)
            else:
                await interaction.response.send_message("You were not registered for this event.", ephemeral=True)


    await ctx.respond(embed=short_embed, view=InitialView(), ephemeral=True)



@bot.event
async def on_reaction_add(reaction, user):
    print(f"Reaction added by {user}: {reaction.emoji}")

    # Ensure we're not reacting to the bot's own reactions
    if user == bot.user:
        return

    if reaction.emoji == '❌' and reaction.message.author == bot.user:
        game_id = extract_game_id(reaction.message.content)
        if game_id and user_has_permission(user, reaction.message.guild):
            await delete_track_data(game_id)
            await reaction.message.edit(content=f"Track data for Game ID {game_id} has been deleted by {user.display_name}.", embed=None)
            # Optionally delete the original message to clean up
            await reaction.message.delete(delay=10)


@bot.event
async def on_ready():
        logger.info(f'We have logged in as {bot.user}')
        await ping_server()

bot.run(token)
