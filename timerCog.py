# timerCog.py
import asyncio
import random
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands

from config import GUILD_ID  # env-driven guild

# --- small helpers -----------------------------------------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def ts(dt: datetime) -> int:
    return int(dt.timestamp())

def make_timer_id(voice_channel_id: int, seq: int) -> str:
    return f"{voice_channel_id}_{seq}"


class TimerCog(commands.Cog):
    """
    Manages a single timer per voice channel:
      - active_timers: running timers (durations, ctx, messages, audio)
      - paused_timers: paused state (remaining, messages, audio)
      - voice_channel_timers: {vc_id: sequence_number}
      - voice_channel_users: {timer_id: [user_ids] or "stopped"}
      - timer_messages: {timer_id: (channel_id, message_id)}
      - timer_tasks: {timer_id: [asyncio.Task, ...]}
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_timers: dict[str, dict] = {}
        self.paused_timers: dict[str, dict] = {}
        self.voice_channel_timers: dict[int, int] = {}
        self.voice_channel_users: dict[str, list[str] | str] = {}
        self.timer_messages: dict[str, tuple[int, int]] = {}
        self.timer_tasks: dict[str, list[asyncio.Task]] = {}

    # ---------------- core actions ----------------

    async def timer_end(
        self,
        ctx: discord.ApplicationContext,
        minutes: float,
        message: str,
        voice_file_path: str | None = None,
        *,
        timer_id: str | None = None,
        edit: bool = False,
        delete_after: float | None = None,  # minutes
    ):
        """Waits `minutes`, then edits/sends message, plays audio, and optionally deletes after."""
        await asyncio.sleep(minutes * 60)

        channel = ctx.channel
        msg_obj: discord.Message | None = None

        if edit and timer_id and timer_id in self.timer_messages:
            # fetch stored message and edit it
            ch_id, m_id = self.timer_messages[timer_id]
            ch = self.bot.get_channel(ch_id) or channel
            try:
                msg_obj = await ch.fetch_message(m_id)
                await msg_obj.edit(content=message)
            except Exception as e:
                print(f"[timer_end] Failed to edit message: {e}")
        else:
            # send new message
            try:
                msg_obj = await channel.send(message)
                if timer_id:
                    self.timer_messages[timer_id] = (channel.id, msg_obj.id)
            except Exception as e:
                print(f"[timer_end] Failed to send message: {e}")

        # optional voice playback
        if voice_file_path and ctx.author.voice and ctx.author.voice.channel:
            vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
            try:
                if not vc or not vc.is_connected():
                    vc = await ctx.author.voice.channel.connect(reconnect=False)
                vc.play(discord.FFmpegPCMAudio(voice_file_path))
                while vc.is_playing():
                    await asyncio.sleep(1)
            except Exception as e:
                # don't fail the whole flow on audio errors
                print(f"[timer_end] Voice playback error: {e}")
            finally:
                try:
                    if vc and vc.is_connected():
                        await vc.disconnect()
                except Exception:
                    pass

        # optional deletion after N minutes
        if delete_after is not None and msg_obj is not None:
            await asyncio.sleep(delete_after * 60)
            try:
                await msg_obj.delete()
            except Exception as e:
                print(f"[timer_end] Failed to delete message: {e}")

    async def play_voice_file(self, ctx: discord.ApplicationContext, voice_file_path: str, delay_seconds: float):
        """Plays `voice_file_path` after `delay_seconds` in user's current VC if possible."""
        await asyncio.sleep(delay_seconds)
        vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
        try:
            if not vc or not vc.is_connected():
                vc = await ctx.author.voice.channel.connect(reconnect=False)
            vc.play(discord.FFmpegPCMAudio(voice_file_path))
            while vc.is_playing():
                await asyncio.sleep(1)
        except Exception as e:
            print(f"[play_voice_file] Playback failed: {e}")
        finally:
            try:
                if vc and vc.is_connected():
                    await vc.disconnect()
            except Exception:
                pass

    # ---------------- utilities ----------------

    def is_user_in_timer(self, user_id: int | str, timer_id: str) -> bool:
        """Check if a user is part of an active timer."""
        arr = self.voice_channel_users.get(timer_id)
        if not isinstance(arr, list):
            return False
        return str(user_id) in [str(u) for u in arr]

    async def _cancel_tasks(self, timer_id: str):
        for task in self.timer_tasks.get(timer_id, []):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.timer_tasks[timer_id] = []

    async def set_timer_stopped(self, timer_id: str, reason: str = "track"):
        """Stop a timer, cancel tasks, and mark message."""
        if timer_id not in self.voice_channel_users:
            return

        # mark stopped + clear state
        self.voice_channel_users[timer_id] = "stopped"
        self.active_timers.pop(timer_id, None)
        self.paused_timers.pop(timer_id, None)
        await self._cancel_tasks(timer_id)

        reason_text = "due to /track command." if reason == "track" else "due to /endtimer command."

        if timer_id in self.timer_messages:
            ch_id, m_id = self.timer_messages[timer_id]
            ch = self.bot.get_channel(ch_id)
            if ch:
                try:
                    msg = await ch.fetch_message(m_id)
                    await msg.edit(content=f"Timer was stopped {reason_text}")
                    # delete after 60s
                    async def _del(m: discord.Message):
                        await asyncio.sleep(60)
                        try:
                            await m.delete()
                        except Exception as e:
                            print(f"[set_timer_stopped] Failed to delete message: {e}")
                    asyncio.create_task(_del(msg))
                except Exception as e:
                    print(f"[set_timer_stopped] Failed to edit/delete message: {e}")
            self.timer_messages.pop(timer_id, None)

    # ---------------- commands ----------------

    @commands.slash_command(guild_ids=[GUILD_ID], name="timer", description="Start a match timer.")
    async def timer(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if not (ctx.author.voice and ctx.author.voice.channel):
            await ctx.respond("You need to be in a voice channel to use this command!", ephemeral=True)
            return

        voice_channel = ctx.author.voice.channel
        # ensure we don't leave a stale client around
        try:
            existing_vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
            if existing_vc:
                await existing_vc.disconnect(force=True)
        except Exception:
            pass

        # connect to VC for start sound
        try:
            vc = await voice_channel.connect(reconnect=True, timeout=10.0)
        except Exception as e:
            await ctx.followup.send("❌ Voice connection failed.")
            print(f"[timer] Voice connection failed: {e}")
            return

        minutes = 80
        extra_time_for_turns = 30
        finals_game_probability = 0.15
        swiss_have_to_win_probability = 0.35
        rand_val = random.random()

        vc_id = voice_channel.id
        self.voice_channel_timers[vc_id] = self.voice_channel_timers.get(vc_id, 0) + 1
        timer_id = make_timer_id(vc_id, self.voice_channel_timers[vc_id])
        self.voice_channel_users[timer_id] = [str(m.id) for m in voice_channel.members]
        if timer_id not in self.timer_tasks:
            self.timer_tasks[timer_id] = []

        try:
            if rand_val <= finals_game_probability:
                # Finals game: no time limit, play finals clip and exit
                _ = await ctx.followup.send(
                    "This is a final game with no time limit! You may ID and restart the match in the same positions if you all have time, "
                    "but in the end it has to have a winner. Play accordingly."
                )
                try:
                    vc.play(discord.FFmpegPCMAudio("./timer/final.mp3"))
                    while vc.is_playing():
                        await asyncio.sleep(1)
                finally:
                    await vc.disconnect(force=True)
                return

            # Swiss WIN&IN vs normal
            end_time = now_utc() + timedelta(minutes=minutes)
            end_ts = ts(end_time)

            if rand_val <= finals_game_probability + swiss_have_to_win_probability:
                sent = await ctx.followup.send(
                    f"WIN & IN: Timer will start now and end <t:{end_ts}:R>. You have to win to make the final cut!"
                )
                self.timer_messages[timer_id] = (sent.channel.id, sent.id)

                # schedule messages / audio
                turns_time = now_utc() + timedelta(minutes=extra_time_for_turns)
                turns_msg = f"Time is over. The active player should finish his turn and take a maximum of 30 minutes to do so - <t:{ts(turns_time)}:R>."
                self.active_timers[timer_id] = {
                    "start_time": now_utc(),
                    "durations": {
                        "main": minutes * 60,
                        "easter_egg": (minutes - 10) * 60,
                        "extra": extra_time_for_turns * 60,
                    },
                    "ctx": ctx,
                    "messages": {"turns": turns_msg, "final": "If no one won until now, the game is a draw. Well Played.", "win_and_in": True},
                    "audio": {"turns": "./timer/ap30minutes.mp3", "final": "./timer/ggboyz.mp3", "easter_egg": "./timer/brasileira10novo.mp3"},
                }

                # start sound
                try:
                    vc.play(discord.FFmpegPCMAudio("./timer/swiss80.mp3"))
                    while vc.is_playing():
                        await asyncio.sleep(1)
                finally:
                    await vc.disconnect(force=True)

                # tasks
                self.timer_tasks[timer_id].append(asyncio.create_task(
                    self.timer_end(ctx, minutes, turns_msg, "./timer/ap30minutes.mp3", timer_id=timer_id, edit=True)
                ))
                self.timer_tasks[timer_id].append(asyncio.create_task(
                    self.play_voice_file(ctx, "./timer/brasileira10novo.mp3", (minutes - 10) * 60)
                ))
                self.timer_tasks[timer_id].append(asyncio.create_task(
                    self.timer_end(ctx, minutes + extra_time_for_turns, "If no one won until now, the game is a draw. Well Played.",
                                   "./timer/ggboyz.mp3", timer_id=timer_id, edit=True, delete_after=1)
                ))

            else:
                sent = await ctx.followup.send(f"Timer will start now and end <t:{end_ts}:R>. Play to win and to your outs.")
                self.timer_messages[timer_id] = (sent.channel.id, sent.id)

                turns_time = now_utc() + timedelta(minutes=extra_time_for_turns)
                turns_msg = f"Time is over. The active player should finish his turn and take a maximum of 30 minutes to do so - <t:{ts(turns_time)}:R>."
                self.active_timers[timer_id] = {
                    "start_time": now_utc(),
                    "durations": {
                        "main": minutes * 60,
                        "easter_egg": (minutes - 10) * 60,
                        "extra": extra_time_for_turns * 60,
                    },
                    "ctx": ctx,
                    "messages": {"turns": turns_msg, "final": "If no one won until now, the game is a draw. Well Played."},
                    "audio": {"turns": "./timer/ap30minutes.mp3", "final": "./timer/ggboyz.mp3", "easter_egg": "./timer/brasileira10novo.mp3"},
                }

                try:
                    vc.play(discord.FFmpegPCMAudio("./timer/timer80.mp3"))
                    while vc.is_playing():
                        await asyncio.sleep(1)
                finally:
                    await vc.disconnect(force=True)

                self.timer_tasks[timer_id].append(asyncio.create_task(
                    self.timer_end(ctx, minutes, turns_msg, "./timer/ap30minutes.mp3", timer_id=timer_id, edit=True)
                ))
                self.timer_tasks[timer_id].append(asyncio.create_task(
                    self.play_voice_file(ctx, "./timer/brasileira10novo.mp3", (minutes - 10) * 60)
                ))
                self.timer_tasks[timer_id].append(asyncio.create_task(
                    self.timer_end(ctx, minutes + extra_time_for_turns, "If no one won until now, the game is a draw. Well Played.",
                                   "./timer/ggboyz.mp3", timer_id=timer_id, edit=True, delete_after=1)
                ))

        except Exception as e:
            print(f"[timer] Fatal error: {e}")
            try:
                if vc and vc.is_connected():
                    await vc.disconnect(force=True)
            except Exception:
                pass

    @commands.slash_command(guild_ids=[GUILD_ID], name="endtimer", description="Manually ends the active timer.")
    async def endtimer(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if not (ctx.author.voice and ctx.author.voice.channel):
            await ctx.respond("You're not in a voice channel.", ephemeral=True)
            return

        vc_id = ctx.author.voice.channel.id
        seq = self.voice_channel_timers.get(vc_id, 0)
        timer_id = make_timer_id(vc_id, seq)

        if self.is_user_in_timer(ctx.author.id, timer_id):
            await self.set_timer_stopped(timer_id, reason="endtimer")
            msg = await ctx.respond("Timer manually ended.")
            await asyncio.sleep(5)
            try:
                await msg.delete()
            except Exception:
                pass
        else:
            await ctx.respond("You're not part of the current timer.", ephemeral=True)

    @commands.slash_command(guild_ids=[GUILD_ID], name="pausetimer", description="Pauses the current timer.")
    async def pausetimer(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if not (ctx.author.voice and ctx.author.voice.channel):
            await ctx.followup.send("You're not in a voice channel.", ephemeral=True)
            return

        vc_id = ctx.author.voice.channel.id
        seq = self.voice_channel_timers.get(vc_id, 0)
        timer_id = make_timer_id(vc_id, seq)

        # must exist & user must be part of it
        if not self.is_user_in_timer(ctx.author.id, timer_id):
            await ctx.followup.send("You're not part of the current timer.", ephemeral=True)
            return
        if timer_id not in self.active_timers:
            await ctx.followup.send("There's no active timer to pause.", ephemeral=True)
            return

        # cancel running tasks
        await self._cancel_tasks(timer_id)

        # compute remaining
        timer_data = self.active_timers.pop(timer_id)
        elapsed = (now_utc() - timer_data["start_time"]).total_seconds()
        durations = timer_data["durations"]
        remaining = {
            "main": max(durations["main"] - elapsed, 0),
            "easter_egg": max(durations["easter_egg"] - elapsed, 0),
            "extra": max(durations["extra"] - elapsed + durations["main"], 0),
        }

        # delete original timer message (if any)
        try:
            ch_id, m_id = self.timer_messages.get(timer_id, (None, None))
            if ch_id and m_id:
                ch = self.bot.get_channel(ch_id)
                if ch:
                    orig = await ch.fetch_message(m_id)
                    await orig.delete()
        except Exception as e:
            print(f"[pausetimer] Error deleting original timer message: {e}")

        remaining_minutes = int(remaining["main"] // 60)
        pause_msg = await ctx.channel.send(f"⏸️ Timer paused – **{remaining_minutes} minutes** remaining.")
        try:
            await ctx.interaction.delete_original_response()
        except Exception:
            pass

        self.paused_timers[timer_id] = {
            "ctx": timer_data["ctx"],
            "remaining": remaining,
            "messages": timer_data["messages"],
            "audio": timer_data["audio"],
            "pause_message": pause_msg,
            "win_and_in": timer_data["messages"].get("win_and_in", False),
        }

    @commands.slash_command(guild_ids=[GUILD_ID], name="resumetimer", description="Resumes a paused timer.")
    async def resumetimer(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if not (ctx.author.voice and ctx.author.voice.channel):
            await ctx.followup.send("You're not in a voice channel.", ephemeral=True)
            return

        vc_id = ctx.author.voice.channel.id
        seq = self.voice_channel_timers.get(vc_id, 0)
        timer_id = make_timer_id(vc_id, seq)

        if timer_id not in self.paused_timers:
            await ctx.followup.send("No paused timer found for your voice channel.", ephemeral=True)
            return

        paused = self.paused_timers.pop(timer_id)

        # delete pause message
        pm = paused.get("pause_message")
        if pm:
            try:
                await pm.delete()
            except Exception:
                pass

        # restore active state
        self.active_timers[timer_id] = {
            "start_time": now_utc(),
            "durations": paused["remaining"],
            "messages": paused["messages"],
            "audio": paused["audio"],
        }
        self.timer_tasks[timer_id] = []

        old_ctx = paused["ctx"]
        turns_msg = paused["messages"]["turns"]
        final_msg = paused["messages"]["final"]
        turns_audio = paused["audio"]["turns"]
        final_audio = paused["audio"]["final"]
        egg_audio = paused["audio"]["easter_egg"]

        main = paused["remaining"]["main"]
        egg = paused["remaining"]["easter_egg"]
        extra = paused["remaining"]["extra"]

        # schedule again with remaining times
        self.timer_tasks[timer_id].append(asyncio.create_task(
            self.timer_end(old_ctx, main / 60, turns_msg, turns_audio, timer_id=timer_id, edit=True)
        ))
        self.timer_tasks[timer_id].append(asyncio.create_task(
            self.play_voice_file(old_ctx, egg_audio, egg)
        ))
        self.timer_tasks[timer_id].append(asyncio.create_task(
            self.timer_end(old_ctx, extra / 60, final_msg, final_audio, timer_id=timer_id, edit=True, delete_after=1)
        ))

        end_time = now_utc() + timedelta(seconds=main)
        resume_text = (
            f"WIN & IN: Timer has been resumed and will end <t:{ts(end_time)}:R>. You have to win to make the final cut!"
            if paused.get("win_and_in", False)
            else f"Timer has been resumed and will end <t:{ts(end_time)}:R>. Play to win and to your outs."
        )
        msg = await ctx.followup.send(resume_text)
        self.timer_messages[timer_id] = (msg.channel.id, msg.id)


def setup(bot: commands.Bot):
    bot.add_cog(TimerCog(bot))
