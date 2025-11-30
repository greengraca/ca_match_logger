# timerCog.py
import asyncio
import contextlib
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

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


# --- voice constants/helpers -------------------------------------------------
VOICE_CONNECT_TIMEOUT = 10.0
VOICE_PLAY_START_TIMEOUT = 6.0   # seconds to wait for is_playing() to flip True

def _same_channel(vc: discord.VoiceClient | None, ch: discord.VoiceChannel | None) -> bool:
    return bool(vc and ch and vc.channel and ch and vc.channel.id == ch.id)

def _ffmpeg_src(path: str) -> discord.FFmpegPCMAudio:
    return discord.FFmpegPCMAudio(path, before_options="-nostdin", options="-vn")


class TimerCog(commands.Cog):
    """
    Joins to play audio and leaves after playback each time.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_timers: dict[str, dict] = {}
        self.paused_timers: dict[str, dict] = {}
        self.voice_channel_timers: dict[int, int] = {}
        self.voice_channel_users: dict[str, list[str] | str] = {}
        self.timer_messages: dict[str, tuple[int, int]] = {}
        self.timer_tasks: dict[str, list[asyncio.Task]] = {}
        self._voice_locks: dict[int, asyncio.Lock] = {}  # per-guild

    # ---------------- voice utils ----------------

    def _vlock(self, gid: int) -> asyncio.Lock:
        lock = self._voice_locks.get(gid)
        if not lock:
            lock = self._voice_locks[gid] = asyncio.Lock()
        return lock

    async def _hard_reset_voice(self, guild: discord.Guild):
        """Forcefully clear any stuck voice state for the guild."""
        vc = guild.voice_client
        with contextlib.suppress(Exception):
            if vc:
                await vc.disconnect(force=True)
        with contextlib.suppress(Exception):
            await guild.change_voice_state(channel=None)
        await asyncio.sleep(0.25)

    async def _ensure_connected(
        self,
        guild: discord.Guild,
        target_ch: Optional[discord.VoiceChannel],
    ) -> Optional[discord.VoiceClient]:
        """Connect or move to target channel; return a connected VoiceClient."""
        if not target_ch:
            return None

        vc = guild.voice_client
        # If connected but wrong channel -> move
        if vc and vc.is_connected():
            if not _same_channel(vc, target_ch):
                with contextlib.suppress(Exception):
                    await vc.move_to(target_ch)
                # best-effort self-deafen after move (if supported)
                with contextlib.suppress(Exception):
                    await guild.change_voice_state(channel=target_ch, self_mute=False, self_deaf=True)
            return guild.voice_client

        # Not connected or stale -> connect (no self_deaf kw here)
        vc = await target_ch.connect(reconnect=False, timeout=VOICE_CONNECT_TIMEOUT)
        # best-effort self-deafen after connect (if supported by this lib)
        with contextlib.suppress(Exception):
            await guild.change_voice_state(channel=target_ch, self_mute=False, self_deaf=True)
        return vc

    async def _play(
        self,
        guild: discord.Guild,
        source_path: Optional[str],
        *,
        channel_id: Optional[int] = None,
        leave_after: bool = True,
    ):
        """
        Play a file in the given guild. If not connected, connect (or move) to channel_id.
        Always disconnect after playback if leave_after=True.

        Adds a hard-reset + single retry for voice 4006 / invalid session cases.
        """
        if not source_path or not guild:
            return

        gid = guild.id
        async with self._vlock(gid):
            # Resolve target channel
            target_ch = None
            if channel_id is not None:
                ch = guild.get_channel(channel_id)
                target_ch = ch if isinstance(ch, discord.VoiceChannel) else None

            async def _connect_and_play() -> bool:
                """Return True if play started and finished; False if it never started."""
                vc = await self._ensure_connected(guild, target_ch)
                if not vc:
                    return False

                if vc.is_playing():
                    vc.stop()

                vc.play(_ffmpeg_src(source_path))

                # Wait for playback to actually start
                waited = 0.0
                while not vc.is_playing() and waited < VOICE_PLAY_START_TIMEOUT:
                    await asyncio.sleep(0.1)
                    waited += 0.1

                if not vc.is_playing():
                    return False

                # Drain
                while vc.is_playing():
                    await asyncio.sleep(0.5)

                return True

            played = False
            try:
                played = await _connect_and_play()
            except discord.errors.ConnectionClosed as e:
                # Voice WS closed (e.g., 4006). We'll hard reset and retry once.
                print(f"[voice] WS closed ({getattr(e, 'code', '???')}). Will hard reset and retry once.")
            except Exception as e:
                print(f"[voice] play error (first attempt): {e}")

            if not played:
                # Hard reset & one retry
                await self._hard_reset_voice(guild)
                try:
                    played = await _connect_and_play()
                except Exception as e:
                    print(f"[voice] play error (after reset): {e}")

            # Always leave after playing so the bot doesn't sit in VC
            if leave_after:
                vc = guild.voice_client
                if vc and vc.is_connected():
                    with contextlib.suppress(Exception):
                        await vc.disconnect(force=True)

    # ---------------- core actions ----------------

    async def timer_end(
        self,
        ctx: discord.ApplicationContext,
        minutes: float,
        message: str,
        voice_file_path: Optional[str] = None,
        *,
        timer_id: Optional[str] = None,
        edit: bool = False,
        delete_after: Optional[float] = None,  # minutes
    ):
        await asyncio.sleep(max(0.0, minutes) * 60)

        channel = ctx.channel
        msg_obj: Optional[discord.Message] = None

        if edit and timer_id and timer_id in self.timer_messages:
            ch_id, m_id = self.timer_messages[timer_id]
            ch = self.bot.get_channel(ch_id) or channel
            try:
                msg_obj = await ch.fetch_message(m_id)
                await msg_obj.edit(content=message)
            except Exception as e:
                print(f"[timer_end] Failed to edit message: {e}")
        else:
            try:
                msg_obj = await channel.send(message)
                if timer_id:
                    self.timer_messages[timer_id] = (channel.id, msg_obj.id)
            except Exception as e:
                print(f"[timer_end] Failed to send message: {e}")

        # play scheduled audio and leave right after
        vcid = None
        if timer_id and timer_id in self.active_timers:
            vcid = self.active_timers[timer_id].get("voice_channel_id")
        if vcid is None and ctx.author.voice and ctx.author.voice.channel:
            vcid = ctx.author.voice.channel.id

        await self._play(ctx.guild, voice_file_path, channel_id=vcid, leave_after=True)

        if delete_after is not None and msg_obj is not None:
            await asyncio.sleep(max(0.0, delete_after) * 60)
            with contextlib.suppress(Exception):
                await msg_obj.delete()

    async def play_voice_file(
        self,
        ctx: discord.ApplicationContext,
        voice_file_path: str,
        delay_seconds: float,
        *,
        timer_id: Optional[str] = None,
    ):
        await asyncio.sleep(max(0.0, delay_seconds))

        vcid = None
        if timer_id and timer_id in self.active_timers:
            vcid = self.active_timers[timer_id].get("voice_channel_id")
        if vcid is None and ctx.author.voice and ctx.author.voice.channel:
            vcid = ctx.author.voice.channel.id

        await self._play(ctx.guild, voice_file_path, channel_id=vcid, leave_after=True)

    # ---------------- utilities ----------------

    def is_user_in_timer(self, user_id: int | str, timer_id: str) -> bool:
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
        if timer_id not in self.voice_channel_users:
            return

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
                    async def _del(m: discord.Message):
                        await asyncio.sleep(60)
                        with contextlib.suppress(Exception):
                            await m.delete()
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

        minutes = 75
        extra_time_for_turns = 15
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
                await ctx.followup.send(
                    "This is a final game with no time limit! You may ID and restart the match in the same positions if you all have time, "
                    "but in the end it has to have a winner. Play accordingly."
                )
                await self._play(ctx.guild, "./timer/final.mp3", channel_id=voice_channel.id, leave_after=True)
                return

            end_time = now_utc() + timedelta(minutes=minutes)
            end_ts = ts(end_time)

            if rand_val <= finals_game_probability + swiss_have_to_win_probability:
                sent = await ctx.followup.send(
                    f"WIN & IN: Timer will start now and end <t:{end_ts}:R>. You have to win to make the final cut!"
                )
                self.timer_messages[timer_id] = (sent.channel.id, sent.id)

                turns_time = now_utc() + timedelta(minutes=extra_time_for_turns)
                turns_msg = f"Time is over. You have 15 minutes to reach a conclusion. Good luck ! - <t:{ts(turns_time)}:R>."
                self.active_timers[timer_id] = {
                    "start_time": now_utc(),
                    "durations": {
                        "main": minutes * 60,
                        "easter_egg": (minutes - 10) * 60,
                        "extra": extra_time_for_turns * 60,
                    },
                    "ctx": ctx,
                    "voice_channel_id": voice_channel.id,
                    "messages": {"turns": turns_msg, "final": "If no one won until now, the game is a draw. Well Played.", "win_and_in": True},
                    "audio": {"turns": "./timer/ap15minutes.mp3", "final": "./timer/ggboyz.mp3", "easter_egg": "./timer/brasileira10novo.mp3"},
                }

                await self._play(ctx.guild, "./timer/timer75.mp3", channel_id=voice_channel.id, leave_after=True)

                self.timer_tasks[timer_id].append(asyncio.create_task(
                    self.timer_end(ctx, minutes, turns_msg, "./timer/ap15minutes.mp3", timer_id=timer_id, edit=True)
                ))
                self.timer_tasks[timer_id].append(asyncio.create_task(
                    self.play_voice_file(ctx, "./timer/brasileira10novo.mp3", (minutes - 10) * 60, timer_id=timer_id)
                ))
                self.timer_tasks[timer_id].append(asyncio.create_task(
                    self.timer_end(ctx, minutes + extra_time_for_turns, "If no one won until now, the game is a draw. Well Played.",
                                   "./timer/ggboyz.mp3", timer_id=timer_id, edit=True, delete_after=1)
                ))

            else:
                sent = await ctx.followup.send(f"Timer will start now and end <t:{end_ts}:R>. Play to win and to your outs.")
                self.timer_messages[timer_id] = (sent.channel.id, sent.id)

                turns_time = now_utc() + timedelta(minutes=extra_time_for_turns)
                turns_msg = f"Time is over. You have 15 minutes to reach a conclusion. Good luck ! - <t:{ts(turns_time)}:R>."
                self.active_timers[timer_id] = {
                    "start_time": now_utc(),
                    "durations": {
                        "main": minutes * 60,
                        "easter_egg": (minutes - 10) * 60,
                        "extra": extra_time_for_turns * 60,
                    },
                    "ctx": ctx,
                    "voice_channel_id": voice_channel.id,
                    "messages": {"turns": turns_msg, "final": "If no one won until now, the game is a draw. Well Played."},
                    "audio": {"turns": "./timer/ap15minutes.mp3", "final": "./timer/ggboyz.mp3", "easter_egg": "./timer/brasileira10novo.mp3"},
                }

                await self._play(ctx.guild, "./timer/timer80.mp3", channel_id=voice_channel.id, leave_after=True)

                self.timer_tasks[timer_id].append(asyncio.create_task(
                    self.timer_end(ctx, minutes, turns_msg, "./timer/ap15minutes.mp3", timer_id=timer_id, edit=True)
                ))
                self.timer_tasks[timer_id].append(asyncio.create_task(
                    self.play_voice_file(ctx, "./timer/brasileira10novo.mp3", (minutes - 10) * 60, timer_id=timer_id)
                ))
                self.timer_tasks[timer_id].append(asyncio.create_task(
                    self.timer_end(ctx, minutes + extra_time_for_turns, "If no one won until now, the game is a draw. Well Played.",
                                   "./timer/ggboyz.mp3", timer_id=timer_id, edit=True, delete_after=1)
                ))

        except Exception as e:
            print(f"[timer] Fatal error: {e}")

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
            with contextlib.suppress(Exception):
                await msg.delete()
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

        if not self.is_user_in_timer(ctx.author.id, timer_id):
            await ctx.followup.send("You're not part of the current timer.", ephemeral=True)
            return
        if timer_id not in self.active_timers:
            await ctx.followup.send("There's no active timer to pause.", ephemeral=True)
            return

        await self._cancel_tasks(timer_id)

        timer_data = self.active_timers.pop(timer_id)
        elapsed = (now_utc() - timer_data["start_time"]).total_seconds()
        durations = timer_data["durations"]
        remaining = {
            "main": max(durations["main"] - elapsed, 0),
            "easter_egg": max(durations["easter_egg"] - elapsed, 0),
            "extra": max(durations["extra"] - elapsed + durations["main"], 0),
        }

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
        with contextlib.suppress(Exception):
            await ctx.interaction.delete_original_response()

        self.paused_timers[timer_id] = {
            "ctx": timer_data["ctx"],
            "remaining": remaining,
            "messages": timer_data["messages"],
            "audio": timer_data["audio"],
            "pause_message": pause_msg,
            "win_and_in": timer_data["messages"].get("win_and_in", False),
            "voice_channel_id": timer_data.get("voice_channel_id"),
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

        pm = paused.get("pause_message")
        if pm:
            with contextlib.suppress(Exception):
                await pm.delete()

        self.active_timers[timer_id] = {
            "start_time": now_utc(),
            "durations": paused["remaining"],
            "messages": paused["messages"],
            "audio": paused["audio"],
            "voice_channel_id": paused.get("voice_channel_id"),
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

        self.timer_tasks[timer_id].append(asyncio.create_task(
            self.timer_end(old_ctx, main / 60, turns_msg, turns_audio, timer_id=timer_id, edit=True)
        ))
        self.timer_tasks[timer_id].append(asyncio.create_task(
            self.play_voice_file(old_ctx, egg_audio, egg, timer_id=timer_id)
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
