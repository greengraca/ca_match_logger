# timerCog.py
import os
import imageio_ffmpeg  # add this import
import asyncio
import contextlib
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord
from discord.ext import commands

from config import GUILD_ID  # env-driven guild


try:
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
    print(f"[voice] Using ffmpeg from imageio-ffmpeg: {FFMPEG_EXE}")
except Exception as e:
    FFMPEG_EXE = "ffmpeg"
    print(f"[voice] Failed to get imageio-ffmpeg binary, falling back to 'ffmpeg': {e}")

# --- env-driven timing -------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default

# Main round duration in minutes
TIMER_MINUTES: float = _env_float("TIMER_MINUTES", 80.0)

# Extra time for turns in minutes
EXTRA_TURNS_MINUTES: float = _env_float("EXTRA_TURNS_MINUTES", 20.0)

# Probability this is a finals game (no time limit)
FINALS_GAME_PROBABILITY: float = _env_float("FINALS_GAME_PROBABILITY", 0.15)

# Probability this is a WIN & IN swiss game (must win to make cut)
SWISS_HAVE_TO_WIN_PROBABILITY: float = _env_float(
    "SWISS_HAVE_TO_WIN_PROBABILITY", 0.35
)

# Brasileira should always play N minutes before main time ends
BRASILEIRA_OFFSET_MINUTES: float = 10.0  # for testing


# --- small helpers -----------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def ts(dt: datetime) -> int:
    return int(dt.timestamp())

def make_timer_id(voice_channel_id: int, seq: int) -> str:
    return f"{voice_channel_id}_{seq}"


# --- voice constants/helpers -------------------------------------------------

VOICE_CONNECT_TIMEOUT = 10.0

def _same_channel(
    vc: Optional[discord.VoiceClient],
    ch: Optional[discord.VoiceChannel],
) -> bool:
    return bool(vc and vc.channel and ch and vc.channel.id == ch.id)


def _voice_prereqs_ok() -> bool:
    # Opus must be loaded and PyNaCl must import (voice crypto)
    if not discord.opus.is_loaded():
        print("[voice] Opus is not loaded")
        return False
    try:
        import nacl  # noqa: F401
    except Exception:
        print("[voice] PyNaCl is not installed; voice cannot work")
        return False
    return True


# def _ffmpeg_src(path: str) -> discord.AudioSource:
#     # Use Opus-encoded output from ffmpeg; avoids PCM encoding path.
#     return discord.FFmpegOpusAudio(path, before_options="-nostdin", options="-vn")

def _ffmpeg_src(path: str) -> discord.AudioSource:
    # Use Opus-encoded output from ffmpeg; avoids PCM encoding path.
    return discord.FFmpegOpusAudio(
        path,
        before_options="-nostdin",
        options="-vn",
        executable=FFMPEG_EXE,  # 👈 key change
    )



# --- Cog ---------------------------------------------------------------------

class TimerCog(commands.Cog):
    """
    Joins to play audio and leaves after playback each time.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # timer_id -> metadata
        self.active_timers: dict[str, dict] = {}
        self.paused_timers: dict[str, dict] = {}

        # voice_channel_id -> latest seq number (for timer_id)
        self.voice_channel_timers: dict[int, int] = {}

        # timer_id -> [user_ids] or "stopped"
        self.voice_channel_users: dict[str, list[str] | str] = {}

        # timer_id -> (channel_id, message_id)
        self.timer_messages: dict[str, tuple[int, int]] = {}

        # timer_id -> list[asyncio.Task]
        self.timer_tasks: dict[str, list[asyncio.Task]] = {}

        # guild_id -> asyncio.Lock (to serialize voice ops per guild)
        self._voice_locks: dict[int, asyncio.Lock] = {}

        print(
            f"[timerCog init] TIMER_MINUTES={TIMER_MINUTES}, "
            f"EXTRA_TURNS_MINUTES={EXTRA_TURNS_MINUTES}, "
            f"FINALS_GAME_PROBABILITY={FINALS_GAME_PROBABILITY}, "
            f"SWISS_HAVE_TO_WIN_PROBABILITY={SWISS_HAVE_TO_WIN_PROBABILITY}, "
            f"BRASILEIRA_OFFSET_MINUTES={BRASILEIRA_OFFSET_MINUTES}"
        )

    # ---------------- voice utils (ONLY inside the class) ----------------

    def _vlock(self, gid: int) -> asyncio.Lock:
        return self._voice_locks.setdefault(gid, asyncio.Lock())

    async def _hard_reset_voice(self, guild: discord.Guild):
        print(f"[voice] Hard-resetting voice for guild {guild.id}")
        with contextlib.suppress(Exception):
            if guild.voice_client:
                await guild.voice_client.disconnect(force=True)
        await asyncio.sleep(0.5)  # give Discord a beat to clear state

    async def _ensure_connected(
        self,
        guild: discord.Guild,
        target_ch: Optional[discord.VoiceChannel],
    ) -> Optional[discord.VoiceClient]:
        """Connect or move to target channel; return a connected VoiceClient."""
        if not target_ch:
            print("[voice] _ensure_connected called with no target channel")
            return None

        vc = guild.voice_client
        if vc and vc.is_connected():
            if not _same_channel(vc, target_ch):
                print(
                    f"[voice] Moving VC in guild {guild.id} from "
                    f"{vc.channel.id if vc.channel else 'None'} to {target_ch.id}"
                )
                with contextlib.suppress(Exception):
                    await vc.move_to(target_ch)
            else:
                print(f"[voice] Already connected to target channel {target_ch.id}")
            return guild.voice_client

        print(f"[voice] Connecting new VC in guild {guild.id} to channel {target_ch.id}")
        return await target_ch.connect(reconnect=True, timeout=VOICE_CONNECT_TIMEOUT)

    async def _play(
        self,
        guild: discord.Guild,
        source_path: Optional[str],
        *,
        channel_id: Optional[int] = None,
        leave_after: bool = True,
    ) -> bool:
        """Connect to VC (or move), play a file, optionally leave afterwards."""
        print(
            f"[voice] _play called: guild={getattr(guild, 'id', None)}, "
            f"source_path={source_path}, channel_id={channel_id}, leave_after={leave_after}"
        )

        if not source_path or not guild:
            print("[voice] Missing source_path or guild, aborting _play")
            return False

        if not _voice_prereqs_ok():
            print("[voice] Prereqs not OK; skipping playback")
            return False

        async with self._vlock(guild.id):
            ch = guild.get_channel(channel_id) if channel_id else None
            if not isinstance(ch, discord.VoiceChannel):
                print(f"[voice] Target channel is not a VoiceChannel: {ch}")
                return False

            async def connect_and_play() -> bool:
                vc = await self._ensure_connected(guild, ch)
                if not vc:
                    print("[voice] Failed to obtain VoiceClient")
                    return False

                print(
                    f"[voice] Starting playback in guild {guild.id}, "
                    f"channel {ch.id}, file={source_path}"
                )
                try:
                    # wait_finish=True returns a Future we can await
                    task = vc.play(_ffmpeg_src(source_path), wait_finish=True)
                except Exception as e:
                    print(f"[voice] vc.play() raised: {e}")
                    return False

                if task is not None:
                    try:
                        err = await task
                        if err:
                            raise err
                    except Exception as e:
                        print(f"[voice] Playback error: {e}")
                        return False

                print(f"[voice] Finished playback in guild {guild.id}, channel {ch.id}")
                return True

            try:
                ok = await connect_and_play()
            except discord.errors.ConnectionClosed as e:
                # e.g. 4006 invalid voice session: full reset then one retry
                print(f"[voice] ConnectionClosed during playback: {e} – hard-resetting and retrying once")
                await self._hard_reset_voice(guild)
                ok = await connect_and_play()

            if leave_after:
                with contextlib.suppress(Exception):
                    if guild.voice_client and guild.voice_client.is_connected():
                        print(f"[voice] Disconnecting from guild {guild.id} voice")
                        await guild.voice_client.disconnect(force=True)
            print(f"[voice] _play returning {ok}")
            return ok

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
        delay_sec = max(0.0, minutes) * 60
        print(
            f"[timer_end] Scheduled fire: timer_id={timer_id}, minutes={minutes}, "
            f"delay_sec={delay_sec}, voice_file_path={voice_file_path}, edit={edit}"
        )
        # Wait until this particular stage (main time, turns, final, etc.)
        await asyncio.sleep(delay_sec)

        print(
            f"[timer_end] Firing: timer_id={timer_id}, message='{message[:40]}...', "
            f"voice_file_path={voice_file_path}"
        )

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

        print(
            f"[timer_end] About to play voice_file_path={voice_file_path} "
            f"for timer_id={timer_id}, vcid={vcid}"
        )

        if voice_file_path and vcid is not None:
            await self._play(ctx.guild, voice_file_path, channel_id=vcid, leave_after=True)

        if delete_after is not None and msg_obj is not None:
            delete_delay_sec = max(0.0, delete_after) * 60
            print(
                f"[timer_end] Scheduling deletion of message in {delete_delay_sec} seconds "
                f"for timer_id={timer_id}"
            )
            await asyncio.sleep(delete_delay_sec)
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
        """Play a given file after some delay, then leave VC."""
        print(
            f"[play_voice_file] Scheduled: timer_id={timer_id}, "
            f"delay_seconds={delay_seconds}, path={voice_file_path}"
        )
        await asyncio.sleep(max(0.0, delay_seconds))

        print(
            f"[play_voice_file] Firing: timer_id={timer_id}, "
            f"delay_seconds={delay_seconds}, path={voice_file_path}"
        )

        vcid = None
        if timer_id and timer_id in self.active_timers:
            vcid = self.active_timers[timer_id].get("voice_channel_id")
        if vcid is None and ctx.author.voice and ctx.author.voice.channel:
            vcid = ctx.author.voice.channel.id

        print(
            f"[play_voice_file] Using vcid={vcid} for timer_id={timer_id}, "
            f"path={voice_file_path}"
        )

        if vcid is None:
            print("[play_voice_file] No vcid resolved; skipping playback")
            return

        await self._play(ctx.guild, voice_file_path, channel_id=vcid, leave_after=True)

    # ---------------- utilities ----------------

    def is_user_in_timer(self, user_id: int | str, timer_id: str) -> bool:
        arr = self.voice_channel_users.get(timer_id)
        if not isinstance(arr, list):
            return False
        return str(user_id) in [str(u) for u in arr]

    async def _cancel_tasks(self, timer_id: str):
        print(f"[cancel_tasks] Cancelling tasks for timer_id={timer_id}")
        for task in self.timer_tasks.get(timer_id, []):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.timer_tasks[timer_id] = []

    async def set_timer_stopped(self, timer_id: str, reason: str = "track"):
        print(f"[set_timer_stopped] timer_id={timer_id}, reason={reason}")
        if timer_id not in self.voice_channel_users:
            print("[set_timer_stopped] timer_id not in voice_channel_users")
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

    # @commands.slash_command(
    #     guild_ids=[GUILD_ID],
    #     name="vtest",
    #     description="Minimal voice connect + play test",
    # )
    # async def vtest(self, ctx: discord.ApplicationContext):
    #     """Minimal voice test: connect, play brasileira clip, disconnect."""
    #     if not (ctx.author.voice and ctx.author.voice.channel):
    #         return await ctx.respond("Join a voice channel first", ephemeral=True)

    #     if not _voice_prereqs_ok():
    #         return await ctx.respond(
    #             "Voice prereqs not OK (Opus / PyNaCl). Check console.", ephemeral=True
    #         )

    #     ch = ctx.author.voice.channel
    #     await ctx.respond("Connecting…")
    #     try:
    #         vc = await ch.connect(reconnect=False, timeout=15)
    #     except Exception as e:
    #         print(f"[vtest error] Voice connect failed: {type(e).__name__}: {e}")
    #         return await ctx.followup.send(f"Connect failed: `{type(e).__name__}: {e}`")

    #     if not vc.is_connected():
    #         return await ctx.followup.send("Voice client not connected ❌")

    #     await ctx.followup.send("Connected. Trying to play audio…")

    #     try:
    #         print("[vtest] Playing timer/brasileira10novo.mp3")
    #         vc.play(discord.FFmpegPCMAudio("timer/brasileira10novo.mp3", executable=FFMPEG_EXE))
    #         await asyncio.sleep(5)
    #     except Exception as e:
    #         print(f"[vtest error] Playback failed: {type(e).__name__}: {e}")
    #         return await ctx.followup.send(f"Play failed: `{type(e).__name__}: {e}`")
    #     finally:
    #         print("[vtest] Disconnecting VC")
    #         await vc.disconnect(force=True)

    @commands.slash_command(guild_ids=[GUILD_ID], name="timer", description="Start a match timer.")
    async def timer(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if not (ctx.author.voice and ctx.author.voice.channel):
            await ctx.respond(
                "You need to be in a voice channel to use this command!",
                ephemeral=True,
            )
            return

        voice_channel = ctx.author.voice.channel

        # Use env-driven values
        minutes = TIMER_MINUTES
        extra_time_for_turns = EXTRA_TURNS_MINUTES
        finals_game_probability = FINALS_GAME_PROBABILITY
        swiss_have_to_win_probability = SWISS_HAVE_TO_WIN_PROBABILITY

        rand_val = random.random()

        print(
            f"[timer] Called by user={ctx.author.id}, guild={ctx.guild.id}, "
            f"voice_channel={voice_channel.id}, minutes={minutes}, "
            f"extra_time_for_turns={extra_time_for_turns}, "
            f"BRASILEIRA_OFFSET_MINUTES={BRASILEIRA_OFFSET_MINUTES}, "
            f"rand_val={rand_val}"
        )

        vc_id = voice_channel.id
        self.voice_channel_timers[vc_id] = self.voice_channel_timers.get(vc_id, 0) + 1
        timer_id = make_timer_id(vc_id, self.voice_channel_timers[vc_id])
        print(f"[timer] Using timer_id={timer_id}")

        self.voice_channel_users[timer_id] = [str(m.id) for m in voice_channel.members]
        if timer_id not in self.timer_tasks:
            self.timer_tasks[timer_id] = []

        try:
            # Finals: no timer, just explanation + final audio
            if rand_val <= finals_game_probability:
                print("[timer] Branch: FINALS (no timer)")
                await ctx.followup.send(
                    "This is a final game with no time limit! You may ID and restart "
                    "the match in the same positions if you all have time, but in the "
                    "end it has to have a winner. Play accordingly."
                )
                await self._play(
                    ctx.guild,
                    "./timer/final.mp3",
                    channel_id=voice_channel.id,
                    leave_after=True,
                )
                return

            end_time = now_utc() + timedelta(minutes=minutes)
            end_ts = ts(end_time)

            # calculate brasileira delay for logging
            brasileira_delay_sec = max((minutes - BRASILEIRA_OFFSET_MINUTES) * 60, 0.0)
            print(
                f"[timer] Calculated brasileira_delay_sec={brasileira_delay_sec} "
                f"({brasileira_delay_sec/60:.2f} minutes from start)"
            )

            # WIN & IN branch
            if rand_val <= finals_game_probability + swiss_have_to_win_probability:
                print("[timer] Branch: WIN & IN")
                sent = await ctx.followup.send(
                    f"WIN & IN: Timer will start now and end <t:{end_ts}:R>. "
                    f"You have to win to make the final cut!"
                )
                self.timer_messages[timer_id] = (sent.channel.id, sent.id)

                turns_time = now_utc() + timedelta(minutes=extra_time_for_turns)
                turns_msg = (
                    f"Time is over. You have {int(extra_time_for_turns)} minutes to reach a conclusion. "
                    f"Good luck ! - <t:{ts(turns_time)}:R>."
                )


                # schedule durations in seconds
                main_seconds = minutes * 60
                extra_seconds = extra_time_for_turns * 60

                self.active_timers[timer_id] = {
                    "start_time": now_utc(),
                    "durations": {
                        "main": main_seconds,
                        # brasileira always N mins before main time ends
                        "easter_egg": brasileira_delay_sec,
                        "extra": extra_seconds,
                    },
                    "ctx": ctx,
                    "voice_channel_id": voice_channel.id,
                    "messages": {
                        "turns": turns_msg,
                        "final": "If no one won until now, the game is a draw. Well Played.",
                        "win_and_in": True,
                    },
                    "audio": {
                        "turns": "./timer/ap20minutes.mp3",
                        "final": "./timer/ggboyz.mp3",
                        "easter_egg": "./timer/brasileira10novo.mp3",
                    },
                }

                # intro audio (WIN & IN) – you can swap to timer75 if you want
                await self._play(
                    ctx.guild,
                    "./timer/timer80.mp3",
                    channel_id=voice_channel.id,
                    leave_after=True,
                )

                # main time end -> turns message + audio
                self.timer_tasks[timer_id].append(
                    asyncio.create_task(
                        self.timer_end(
                            ctx,
                            minutes,
                            turns_msg,
                            "./timer/ap20minutes.mp3",
                            timer_id=timer_id,
                            edit=True,
                        )
                    )
                )
                # brasileira N mins before end of main time
                self.timer_tasks[timer_id].append(
                    asyncio.create_task(
                        self.play_voice_file(
                            ctx,
                            "./timer/brasileira10novo.mp3",
                            brasileira_delay_sec,
                            timer_id=timer_id,
                        )
                    )
                )
                # final message after extra time
                self.timer_tasks[timer_id].append(
                    asyncio.create_task(
                        self.timer_end(
                            ctx,
                            minutes + extra_time_for_turns,
                            "If no one won until now, the game is a draw. Well Played.",
                            "./timer/ggboyz.mp3",
                            timer_id=timer_id,
                            edit=True,
                            delete_after=1,
                        )
                    )
                )

            else:
                # Regular swiss round
                print("[timer] Branch: Regular swiss")
                sent = await ctx.followup.send(
                    f"Timer will start now and end <t:{end_ts}:R>. "
                    f"Play to win and to your outs."
                )
                self.timer_messages[timer_id] = (sent.channel.id, sent.id)

                turns_time = now_utc() + timedelta(minutes=extra_time_for_turns)
                turns_msg = (
                    f"Time is over. You have {int(extra_time_for_turns)} minutes to reach a conclusion. "
                    f"Good luck ! - <t:{ts(turns_time)}:R>."
                )


                main_seconds = minutes * 60
                extra_seconds = extra_time_for_turns * 60

                self.active_timers[timer_id] = {
                    "start_time": now_utc(),
                    "durations": {
                        "main": main_seconds,
                        "easter_egg": brasileira_delay_sec,
                        "extra": extra_seconds,
                    },
                    "ctx": ctx,
                    "voice_channel_id": voice_channel.id,
                    "messages": {
                        "turns": turns_msg,
                        "final": "If no one won until now, the game is a draw. Well Played.",
                    },
                    "audio": {
                        "turns": "./timer/ap20minutes.mp3",
                        "final": "./timer/ggboyz.mp3",
                        "easter_egg": "./timer/brasileira10novo.mp3",
                    },
                }

                # intro audio (regular swiss)
                await self._play(
                    ctx.guild,
                    "./timer/timer80.mp3",
                    channel_id=voice_channel.id,
                    leave_after=True,
                )

                self.timer_tasks[timer_id].append(
                    asyncio.create_task(
                        self.timer_end(
                            ctx,
                            minutes,
                            turns_msg,
                            "./timer/ap20minutes.mp3",
                            timer_id=timer_id,
                            edit=True,
                        )
                    )
                )
                self.timer_tasks[timer_id].append(
                    asyncio.create_task(
                        self.play_voice_file(
                            ctx,
                            "./timer/brasileira10novo.mp3",
                            brasileira_delay_sec,
                            timer_id=timer_id,
                        )
                    )
                )
                self.timer_tasks[timer_id].append(
                    asyncio.create_task(
                        self.timer_end(
                            ctx,
                            minutes + extra_time_for_turns,
                            "If no one won until now, the game is a draw. Well Played.",
                            "./timer/ggboyz.mp3",
                            timer_id=timer_id,
                            edit=True,
                            delete_after=1,
                        )
                    )
                )

            print(
                f"[timer] Scheduled tasks for timer_id={timer_id}: "
                f"{len(self.timer_tasks[timer_id])} tasks, "
                f"brasileira_delay_sec={brasileira_delay_sec}"
            )

        except Exception as e:
            print(f"[timer] Fatal error: {e}")

    @commands.slash_command(
        guild_ids=[GUILD_ID],
        name="endtimer",
        description="Manually ends the active timer.",
    )
    async def endtimer(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if not (ctx.author.voice and ctx.author.voice.channel):
            await ctx.respond("You're not in a voice channel.", ephemeral=True)
            return

        vc_id = ctx.author.voice.channel.id
        seq = self.voice_channel_timers.get(vc_id, 0)
        timer_id = make_timer_id(vc_id, seq)

        print(f"[endtimer] Called by user={ctx.author.id}, timer_id={timer_id}")

        if self.is_user_in_timer(ctx.author.id, timer_id):
            await self.set_timer_stopped(timer_id, reason="endtimer")
            msg = await ctx.respond("Timer manually ended.")
            await asyncio.sleep(5)
            with contextlib.suppress(Exception):
                await msg.delete()
        else:
            await ctx.respond("You're not part of the current timer.", ephemeral=True)

    @commands.slash_command(
        guild_ids=[GUILD_ID],
        name="pausetimer",
        description="Pauses the current timer.",
    )
    async def pausetimer(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if not (ctx.author.voice and ctx.author.voice.channel):
            await ctx.followup.send("You're not in a voice channel.", ephemeral=True)
            return

        vc_id = ctx.author.voice.channel.id
        seq = self.voice_channel_timers.get(vc_id, 0)
        timer_id = make_timer_id(vc_id, seq)

        print(f"[pausetimer] Called by user={ctx.author.id}, timer_id={timer_id}")

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
            # how long until brasileira from now
            "easter_egg": max(durations["easter_egg"] - elapsed, 0),
            # how long until final from now (main + extra - elapsed)
            "extra": max(durations["extra"] - elapsed + durations["main"], 0),
        }

        print(
            f"[pausetimer] elapsed={elapsed}, durations={durations}, remaining={remaining}"
        )

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
        pause_msg = await ctx.channel.send(
            f"⏸️ Timer paused – **{remaining_minutes} minutes** remaining."
        )
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

    @commands.slash_command(
        guild_ids=[GUILD_ID],
        name="resumetimer",
        description="Resumes a paused timer.",
    )
    async def resumetimer(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        if not (ctx.author.voice and ctx.author.voice.channel):
            await ctx.followup.send("You're not in a voice channel.", ephemeral=True)
            return

        vc_id = ctx.author.voice.channel.id
        seq = self.voice_channel_timers.get(vc_id, 0)
        timer_id = make_timer_id(vc_id, seq)

        print(f"[resumetimer] Called by user={ctx.author.id}, timer_id={timer_id}")

        if timer_id not in self.paused_timers:
            await ctx.followup.send(
                "No paused timer found for your voice channel.", ephemeral=True
            )
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

        print(
            f"[resumetimer] remaining main={main}, egg={egg}, extra={extra}, "
            f"messages={paused['messages']}, audio={paused['audio']}"
        )

        # Main time finish -> turns
        self.timer_tasks[timer_id].append(
            asyncio.create_task(
                self.timer_end(
                    old_ctx,
                    main / 60,
                    turns_msg,
                    turns_audio,
                    timer_id=timer_id,
                    edit=True,
                )
            )
        )
        # Brasileira at remaining 'egg' seconds from now
        self.timer_tasks[timer_id].append(
            asyncio.create_task(
                self.play_voice_file(
                    old_ctx,
                    egg_audio,
                    egg,
                    timer_id=timer_id,
                )
            )
        )
        # Final after remaining 'extra' seconds
        self.timer_tasks[timer_id].append(
            asyncio.create_task(
                self.timer_end(
                    old_ctx,
                    extra / 60,
                    final_msg,
                    final_audio,
                    timer_id=timer_id,
                    edit=True,
                    delete_after=1,
                )
            )
        )

        print(
            f"[resumetimer] Scheduled {len(self.timer_tasks[timer_id])} tasks again "
            f"for timer_id={timer_id}"
        )

        end_time = now_utc() + timedelta(seconds=main)
        resume_text = (
            f"WIN & IN: Timer has been resumed and will end <t:{ts(end_time)}:R>. "
            f"You have to win to make the final cut!"
            if paused.get("win_and_in", False)
            else f"Timer has been resumed and will end <t:{ts(end_time)}:R>. "
            f"Play to win and to your outs."
        )
        msg = await ctx.followup.send(resume_text)
        self.timer_messages[timer_id] = (msg.channel.id, msg.id)


def setup(bot: commands.Bot):
    bot.add_cog(TimerCog(bot))
