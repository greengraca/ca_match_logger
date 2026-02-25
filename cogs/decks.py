from typing import Annotated

import discord
from discord.ext import commands
from discord.commands import slash_command, Option
from collections import defaultdict
from rapidfuzz import fuzz

from config import GUILD_ID, IS_DEV
from db import decks as decks_col
from utils.text import capitalize_words, format_deck_name, paginate_text, MAX_EMBED_CHARS
from utils.ephemeral import should_be_ephemeral
from utils.views import PaginatorView




# Top-level autocomplete so it works inside annotations (no self reference)
async def deck_autocomplete(ctx: discord.AutocompleteContext):
    cursor = decks_col.find({}, {"name": 1, "_id": 0})
    names = [d["name"] async for d in cursor]
    needle = (ctx.value or "").lower()
    return [n for n in names if needle in n.lower()][:25]


class Decks(commands.Cog):
    def __init__(self, bot): 
        self.bot = bot

    async def deck_exists(self, name: str) -> bool:
        return await decks_col.find_one({"name": name}) is not None

    @slash_command(guild_ids=[GUILD_ID], name="listdecks", description="List all decks in DB.")
    async def list_decks(self, ctx: discord.ApplicationContext):
        cursor = decks_col.find({}, {"name": 1, "_id": 0})
        names = sorted([capitalize_words(d["name"]) async for d in cursor], key=str.lower)
        eph = should_be_ephemeral(ctx)

        if not names:
            await ctx.respond(embed=discord.Embed(title="Decks in the database", description="_No decks found._"), ephemeral=eph)
            return

        # Discord embed description hard-limit is 4096 chars.
        pages = paginate_text(names, header="", limit=MAX_EMBED_CHARS)

        if len(pages) == 1:
            await ctx.respond(embed=discord.Embed(title="Decks in the database", description=pages[0]), ephemeral=eph)
            return

        title = "Decks in the database"
        first = discord.Embed(title=f"{title} (Page 1/{len(pages)})", description=pages[0])
        view = PaginatorView(author=ctx.author, pages=pages, title=title)
        await ctx.respond(embed=first, view=view, ephemeral=eph)

    @slash_command(guild_ids=[GUILD_ID], name="newdeck", description="Add a new deck.")
    async def new_deck(
        self,
        ctx: discord.ApplicationContext,
        deck: Annotated[str, Option(str, "Deck name (supports A/B form)")],
    ):
        deck_to_save = format_deck_name(deck)
        display = capitalize_words(deck_to_save)

        if await self.deck_exists(deck_to_save):
            await ctx.respond(
                embed=discord.Embed(
                    title="Deck already exists",
                    description=f"{display} is already in the database.",
                    color=0xFF0000,
                )
            )
            return

        existing = [d["name"] async for d in decks_col.find({}, {"name": 1})]
        matches = defaultdict(int)
        for ex in existing:
            score = fuzz.ratio(deck_to_save.lower(), ex.lower())
            if score >= 85:
                matches[ex] = max(matches[ex], score)
            if deck_to_save.lower().startswith(ex.lower()) or ex.lower().startswith(deck_to_save.lower()):
                matches[ex] = max(matches[ex], score)

        if matches:
            suggestions = "\n".join(
                f"- {capitalize_words(n)} ({s:.0f}% match)" for n, s in sorted(matches.items(), key=lambda x: x[1], reverse=True)
            )
            warning = (
                f"A similar deck already exists. Add **{display}** anyway?\n\n"
                f"Possible matches:\n{suggestions}"
            )
            yes = discord.ui.Button(label="Yes, add it", style=discord.ButtonStyle.green)
            cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.red)
            view = discord.ui.View(timeout=30)

            async def confirm(_):
                await decks_col.insert_one({"name": deck_to_save})
                await ctx.respond(
                    embed=discord.Embed(
                        title="New deck added",
                        description=f"{display} was successfully added.",
                        color=0xFF0000 if IS_DEV else 0x00FF00,
                    )
                )
                await ctx.edit(view=None)

            async def reject(_):
                await ctx.respond(
                    embed=discord.Embed(
                        title="Deck addition cancelled",
                        description="The deck was not added.",
                        color=0xFFCC00,
                    )
                )
                await ctx.edit(view=None)

            yes.callback = confirm
            cancel.callback = reject
            view.add_item(yes)
            view.add_item(cancel)
            await ctx.respond(warning, view=view, ephemeral=True)
            return

        await decks_col.insert_one({"name": deck_to_save})
        await ctx.respond(
            embed=discord.Embed(
                title="New deck added",
                description=f"{display} was successfully added.",
                color=0xFF0000 if IS_DEV else 0x00FF00,
            ),
            ephemeral=should_be_ephemeral(ctx)
        )


def setup(bot):
    bot.add_cog(Decks(bot))
