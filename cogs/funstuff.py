# cogs/funstuff.py
import discord
from discord.ext import commands
from discord.commands import slash_command
from config import GUILD_ID, IS_DEV
from utils.ephemeral import should_be_ephemeral
from utils.moxfield_client import fetch_json

# Map button label -> Moxfield deck ID
DECKS = {
    "Kodama/Thrasios": "Jnfr7xWDIkWEOPHEQ4MPAw",   # current URL id
    "Zimone and Dina": "76SaehOxIUi2-I2pfUVYJg",
    "Tuvasa":          "SdNZLo6Eb0mDMx6ecTlZDQ",
    "Sakadama":        "du1jbZdQiUKUq12B2z7lmg",
    "Korvold":         "61sGEQQvJk2gAReAsaDx8A",
    "Teval":           "6__-KPMXOE6Its6HQK6LJg",
}

COUNTERS = {
    "Fierce Guardianship", "Tishana's Tidebinder", "Otawara, Soaring City",
    "Manglehorn", "Subtlety", "Dispel", "Force of Will", "Delay", "Mana Drain",
    "Flusterstorm", "Spell Pierce", "Muddle the Mixture", "Miscast", "Trickbind",
    "Misdirection", "Swan Song", "An Offer You Can't Refuse", "Mental Misstep",
    "Mindbreak Trap", "Force of Negation", "Spell Snare", "Stern Scolding",
    "Pact of Negation", "Pyroblast", "Red Elemental Blast", "Blue Elemental Blast"
}
BOUNCES = {"Snap", "Cyclonic Rift", "Alchemist's Retrieval", "Chain of Vapor"}
REMOVAL = {"Legolas's Quick Reflexes", "Archdruid's Charm", "Boseiju, Who Endures"}


class DeckSelectView(discord.ui.View):
    def __init__(self, author_id: int, *, timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id

        # Create a row of buttons (Discord max 5 per row; we have 6 → two rows)
        for i, label in enumerate(DECKS.keys()):
            style = discord.ButtonStyle.primary if i == 0 else discord.ButtonStyle.secondary
            self.add_item(DeckButton(label=label, style=style, row=0 if i < 5 else 1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only the command invoker can press
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This prompt isn't for you 👀", ephemeral=True)
            return False
        return True


class DeckButton(discord.ui.Button):
    async def callback(self, interaction: discord.Interaction):
        deck_label = self.label
        deck_id = DECKS[deck_label]
        url = f"https://api.moxfield.com/v2/decks/all/{deck_id}"

        # Acknowledge quickly (keeps the same ephemerality as the original message)
        await interaction.response.defer()

        # Fetch & compute
        try:
            data = await fetch_json(url)
        except Exception:
            await interaction.followup.send("Failed to fetch deck information from Moxfield.", ephemeral=True)
            return

        mainboard = data.get("mainboard", {}) or {}
        names = mainboard.keys()

        counters_count = sum(1 for c in names if c in COUNTERS)
        bounces_count = sum(1 for c in names if c in BOUNCES)
        removal_count = sum(1 for c in names if c in REMOVAL)

        desc = f"**Deck:** {deck_label}\n\nCounters: {counters_count}\n\nBounces: {bounces_count}\n\nRemoval: {removal_count}"
        embed = discord.Embed(
            title="Abegão's Interaction",
            description=desc,
            color=0xFF0000 if IS_DEV else 0x00FF00
        )

        # Edit the original prompt with results and remove buttons
        await interaction.edit_original_response(content=None, embed=embed, view=None)


class FunStuff(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @slash_command(guild_ids=[GUILD_ID], name="abegasiosinterasios",
                   description="Lists how much interaction Abegão has in his deck.")
    async def abegasios_interasios(self, ctx: discord.ApplicationContext):
        ephemeral = should_be_ephemeral(ctx)
        view = DeckSelectView(author_id=ctx.author.id)

        content = "Which deck is abegão playing?"
        # Initial prompt with buttons
        await ctx.respond(content, view=view, ephemeral=ephemeral)


def setup(bot):
    bot.add_cog(FunStuff(bot))