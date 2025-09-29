# cogs/events.py
from datetime import datetime, timezone
import discord
from discord.ext import commands
from discord.commands import slash_command
from typing import List

from config import GUILD_ID
from db import event_registrations
from utils.perms import is_mod

REG_CLOSE_SECS = 600  # registration closes 10 minutes before start

class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @slash_command(guild_ids=[GUILD_ID], name="events", description="View and register for current events.")
    async def events(self, ctx: discord.ApplicationContext):
        eph = True
        await ctx.defer(ephemeral=eph)

        # Fetch & pick the next event (soonest start time >= now; fallback: earliest)
        guild_events: List[discord.ScheduledEvent] = await ctx.guild.fetch_scheduled_events()
        if not guild_events:
            await ctx.followup.send("There are no scheduled events.", ephemeral=eph)
            return

        now = datetime.now(timezone.utc)
        future = [e for e in guild_events if e.start_time]
        future = sorted(future, key=lambda e: e.start_time or now)
        candidates = [e for e in future if e.start_time and e.start_time >= now] or future
        current_event = candidates[0]

        # Current count
        registration_count = await event_registrations.count_documents({"event_id": str(current_event.id)})
        event_time_str = f"<t:{int(current_event.start_time.timestamp())}:F>"

        short_embed = discord.Embed(
            title="🏆 Scheduled Events 🏆",
            description="Showing the next event.",
            color=0x00BFFF
        )
        short_embed.add_field(name="Name", value=current_event.name, inline=False)
        short_embed.add_field(name="Start Date", value=event_time_str, inline=False)
        short_embed.add_field(name="Registered", value=f"{registration_count} participants", inline=False)

        # ----- Views & buttons (capture ctx/current_event in closures) -----

        class OpenDetailsButton(discord.ui.Button):
            def __init__(self):
                super().__init__(label="Open details / Register", style=discord.ButtonStyle.primary)

            async def callback(self, interaction: discord.Interaction):
                if interaction.user.id != ctx.author.id:
                    await interaction.response.send_message("This is not your interaction.", ephemeral=True)
                    return

                now2 = datetime.now(timezone.utc)
                registration_open = (current_event.start_time - now2).total_seconds() > REG_CLOSE_SECS

                existing = await event_registrations.find_one({
                    "event_id": str(current_event.id),
                    "user_id": str(interaction.user.id)
                })

                reg_count2 = await event_registrations.count_documents({"event_id": str(current_event.id)})
                detailed_embed = discord.Embed(
                    title=current_event.name,
                    description=current_event.description or "No description provided.",
                    color=0x00ff00
                )
                cover = getattr(current_event, "cover", None)  # be defensive re: lib differences
                if cover:
                    detailed_embed.set_image(url=cover)
                detailed_embed.add_field(name="Start Time", value=event_time_str, inline=False)
                detailed_embed.add_field(name="Registered Participants", value=str(reg_count2), inline=False)

                view = discord.ui.View(timeout=120)
                if registration_open:
                    if existing:
                        view.add_item(AlreadyRegisteredButton())
                        view.add_item(UnregisterButton())
                    else:
                        view.add_item(RegisterButton())
                else:
                    view.add_item(discord.ui.Button(
                        label="Registration Closed", style=discord.ButtonStyle.secondary, disabled=True
                    ))

                await interaction.response.edit_message(embed=detailed_embed, view=view)

        class RegisterButton(discord.ui.Button):
            def __init__(self):
                super().__init__(label="Register", style=discord.ButtonStyle.success)

            async def callback(self, interaction: discord.Interaction):
                if (current_event.start_time - datetime.now(timezone.utc)).total_seconds() <= REG_CLOSE_SECS:
                    await interaction.response.send_message(
                        f"❌ Registration is closed ({REG_CLOSE_SECS // 60} minutes before start).",
                        ephemeral=True
                    )
                    return

                # Idempotent upsert
                await event_registrations.update_one(
                    {"event_id": str(current_event.id), "user_id": str(interaction.user.id)},
                    {"$setOnInsert": {"timestamp": datetime.now(timezone.utc)}},
                    upsert=True
                )

                self.disabled = True
                await interaction.response.edit_message(view=self.view)
                await interaction.followup.send(f"✅ You are registered for **{current_event.name}**.", ephemeral=True)

        class UnregisterButton(discord.ui.Button):
            def __init__(self):
                super().__init__(label="❌ Unregister", style=discord.ButtonStyle.danger)

            async def callback(self, interaction: discord.Interaction):
                result = await event_registrations.delete_one({
                    "event_id": str(current_event.id),
                    "user_id": str(interaction.user.id)
                })
                if result.deleted_count > 0:
                    await interaction.response.send_message(
                        f"❌ You have been unregistered from **{current_event.name}**.",
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message("You were not registered for this event.", ephemeral=True)

        class AlreadyRegisteredButton(discord.ui.Button):
            def __init__(self):
                super().__init__(label="✅ Already Registered", style=discord.ButtonStyle.success, disabled=True)

        class SeeParticipantsButton(discord.ui.Button):
            def __init__(self):
                super().__init__(label="Participant Details (MOD ONLY)", style=discord.ButtonStyle.secondary)

            async def callback(self, interaction: discord.Interaction):
                if not is_mod(interaction.user):
                    await interaction.response.send_message("You don’t have permission to view this.", ephemeral=True)
                    return

                participants = await event_registrations.find(
                    {"event_id": str(current_event.id)}
                ).to_list(length=500)

                if not participants:
                    await interaction.response.send_message("No participants registered yet.", ephemeral=True)
                    return

                embed = discord.Embed(title="Participant Details", color=0xAAAAAA)
                for p in participants:
                    user = ctx.guild.get_member(int(p["user_id"]))
                    who = user.mention if user else f"`{p['user_id']}`"
                    ts = p.get("timestamp")
                    ts_str = f"<t:{int(ts.timestamp())}:R>" if ts else "Unknown"
                    embed.add_field(name=who, value=f"Registered {ts_str}", inline=False)

                await interaction.response.send_message(embed=embed, ephemeral=True)

        class InitialView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.add_item(OpenDetailsButton())
                if is_mod(ctx.author):
                    self.add_item(SeeParticipantsButton())

        await ctx.followup.send(embed=short_embed, view=InitialView(), ephemeral=eph)


def setup(bot):
    bot.add_cog(Events(bot))
