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

from config import GUILD_ID, IS_DEV  # env-driven guild + dev flag
from utils.timer_embed import PHASE_COLORS, build_timer_embed, pick_phase


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
TIMER_MINUTES: float = _env_float("TIMER_MINUTES", 75.0)

# Extra time for turns in minutes
EXTRA_TURNS_MINUTES: float = _env_float("EXTRA_TURNS_MINUTES", 15.0)

# Probability this is a finals game (no time limit)
FINALS_GAME_PROBABILITY: float = _env_float("FINALS_GAME_PROBABILITY", 0.15)

# Probability this is a WIN & IN swiss game (must win to make cut)
SWISS_HAVE_TO_WIN_PROBABILITY: float = _env_float(
    "SWISS_HAVE_TO_WIN_PROBABILITY", 0.35
)

# Brasileira should always play N minutes before main time ends
BRASILEIRA_OFFSET_MINUTES: float = 10.0  # for testing

# How often the live embed re-edits (minutes). At width 30 over a 90-min game each
# bar cell ~= 3 min, so a 3-min tick advances ~one cell per update.
TIMER_UPDATE_INTERVAL_MINUTES: float = _env_float("TIMER_UPDATE_INTERVAL_MINUTES", 3.0)

# Audio file paths (override via env if needed)
INTRO_AUDIO: str      = os.getenv("TIMER_INTRO_AUDIO", "./timer/timer75.mp3")
TURNS_AUDIO: str      = os.getenv("TIMER_TURNS_AUDIO", "./timer/ap15minutes.mp3")
EASTER_EGG_AUDIO: str = os.getenv("TIMER_EGG_AUDIO", "./timer/brasileira10novo.mp3")
FINAL_AUDIO: str      = os.getenv("TIMER_FINAL_AUDIO", "./timer/ggboyz.mp3")
FINALS_AUDIO: str     = os.getenv("TIMER_FINALS_AUDIO", "./timer/final.mp3")


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


def _ffmpeg_src(path: str) -> discord.AudioSource:
    # Use Opus-encoded output from ffmpeg; avoids PCM encoding path.
    return discord.FFmpegOpusAudio(
        path,
        before_options="-nostdin",
        options="-vn",
        executable=FFMPEG_EXE,
    )


# --- Cog ---------------------------------------------------------------------

class TimerCog(commands.Cog):
    """
    Joins to play audio and leaves after playback each time.
    Posts a live, phase-colored embed with a progress bar that updates periodically.
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
            f"BRASILEIRA_OFFSET_MINUTES={BRASILEIRA_OFFSET_MINUTES}, "
            f"TIMER_UPDATE_INTERVAL_MINUTES={TIMER_UPDATE_INTERVAL_MINUTES}"
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

    # ---------------- struct cleanup ----------------

    def _cleanup_timer_structs(self, timer_id: str) -> None:
        self.active_timers.pop(timer_id, None)
        self.paused_timers.pop(timer_id, None)
        self.timer_messages.pop(timer_id, None)
        self.timer_tasks.pop(timer_id, None)
        self.voice_channel_users.pop(timer_id, None)

    # ---------------- audio-only tasks ----------------

    async def _audio_at(self, delay_sec: float, audio_path: str, timer_id: str, voice_channel_id: int):
        """Sleep, then play audio. No message editing."""
        await asyncio.sleep(max(0.0, delay_sec))
        if timer_id not in self.active_timers:
            return
        for g in self.bot.guilds:
            if g.get_channel(voice_channel_id):
                await self._play(g, audio_path, channel_id=voice_channel_id, leave_after=True)
                return

    async def _final_audio(self, delay_sec: float, audio_path: str, timer_id: str,
                           voice_channel_id: int, draw_event: asyncio.Event):
        """Sleep, play draw audio, then flag the embed loop to show the draw phase."""
        await asyncio.sleep(max(0.0, delay_sec))
        if timer_id not in self.active_timers:
            return
        guild = next((g for g in self.bot.guilds if g.get_channel(voice_channel_id)), None)
        if guild:
            await self._play(guild, audio_path, channel_id=voice_channel_id, leave_after=True)
        data = self.active_timers.get(timer_id)
        if data:
            data["phase_override"] = "draw"
        draw_event.set()

    # ---------------- embed update loop (sole message editor) ----------------

    async def _embed_update_loop(self, timer_id: str, vc_name: str):
        interval = max(30.0, TIMER_UPDATE_INTERVAL_MINUTES * 60.0)
        while True:
            if timer_id not in self.active_timers:
                return
            data = self.active_timers[timer_id]
            elapsed = (now_utc() - data["start_time"]).total_seconds()
            durations = data["durations"]
            main_dur, extra_dur = durations["main"], durations["extra"]
            remaining_main = max(0.0, main_dur - elapsed)
            remaining_total = max(0.0, main_dur + extra_dur - elapsed)

            orig = data.get("original_durations") or durations
            end_ts_main = ts(data["start_time"] + timedelta(seconds=main_dur))
            end_ts_final = ts(data["start_time"] + timedelta(seconds=main_dur + extra_dur))
            phase = pick_phase(remaining_main, remaining_total, data.get("phase_override"))

            embed = build_timer_embed(
                vc_name=vc_name, phase=phase,
                main_total=orig["main"], extra_total=orig["extra"],
                remaining_main=remaining_main, remaining_total=remaining_total,
                end_ts_main=end_ts_main, end_ts_final=end_ts_final,
                win_and_in=data.get("win_and_in", False),
                title_prefix="(DEV) " if IS_DEV else "",
            )

            msg_info = self.timer_messages.get(timer_id)
            if not msg_info:
                print(f"[timer/loop] no message tracked for {timer_id}, exiting")
                return
            ch_id, m_id = msg_info
            try:
                ch = self.bot.get_channel(ch_id)
                if ch is None:
                    self._cleanup_timer_structs(timer_id)
                    return
                await ch.get_partial_message(m_id).edit(embed=embed)
            except discord.NotFound:
                print(f"[timer/loop] message deleted externally for {timer_id}, cleaning up")
                self._cleanup_timer_structs(timer_id)
                return
            except Exception as e:
                print(f"[timer/loop] edit failed for {timer_id}: {e}")

            if phase == "draw":
                await asyncio.sleep(60)
                with contextlib.suppress(Exception):
                    ch = self.bot.get_channel(ch_id)
                    if ch:
                        await ch.get_partial_message(m_id).delete()
                self._cleanup_timer_structs(timer_id)
                return

            draw_event = data.get("draw_event")
            if draw_event and not draw_event.is_set():
                try:
                    await asyncio.wait_for(draw_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass  # normal tick
            else:
                await asyncio.sleep(interval)

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
        if timer_id not in self.active_timers and timer_id not in self.paused_timers:
            print("[set_timer_stopped] no active/paused timer for this id")
            return

        await self._cancel_tasks(timer_id)

        reason_text = "due to /track command." if reason == "track" else "due to /endtimer command."

        if timer_id in self.timer_messages:
            ch_id, m_id = self.timer_messages[timer_id]
            ch = self.bot.get_channel(ch_id)
            if ch:
                try:
                    msg = await ch.fetch_message(m_id)
                    await msg.edit(content=f"Timer was stopped {reason_text}", embed=None)

                    async def _del(m: discord.Message):
                        await asyncio.sleep(60)
                        with contextlib.suppress(Exception):
                            await m.delete()

                    asyncio.create_task(_del(msg))
                except Exception as e:
                    print(f"[set_timer_stopped] Failed to edit/delete message: {e}")

        self._cleanup_timer_structs(timer_id)

    # ---------------- core timer start ----------------

    async def _send_finals(self, ctx: discord.ApplicationContext, voice_channel: discord.VoiceChannel):
        """Final game: no countdown, no progress bar — just an embed + audio."""
        embed = discord.Embed(
            title=f"{'(DEV) ' if IS_DEV else ''}⏱️ {voice_channel.name} — Final Game",
            description=("This is a final game with **no time limit**. You may ID and restart "
                         "in the same seats if you all have time, but it must end with a winner. "
                         "Play accordingly."),
            color=PHASE_COLORS["running"],
        )
        await ctx.followup.send(embed=embed)
        await self._play(ctx.guild, FINALS_AUDIO, channel_id=voice_channel.id, leave_after=True)

    async def _start_timed(self, ctx: discord.ApplicationContext,
                           voice_channel: discord.VoiceChannel, *, win_and_in: bool):
        """Start a timed round (regular or WIN & IN): live embed + 4 tasks."""
        main_seconds = TIMER_MINUTES * 60.0
        extra_seconds = EXTRA_TURNS_MINUTES * 60.0
        egg_delay = max((TIMER_MINUTES - BRASILEIRA_OFFSET_MINUTES) * 60.0, 0.0)

        vc_id = voice_channel.id
        self.voice_channel_timers[vc_id] = self.voice_channel_timers.get(vc_id, 0) + 1
        timer_id = make_timer_id(vc_id, self.voice_channel_timers[vc_id])
        self.voice_channel_users[timer_id] = [str(m.id) for m in voice_channel.members]
        self.timer_tasks[timer_id] = []
        print(f"[timer] Using timer_id={timer_id}, win_and_in={win_and_in}")

        start_time = now_utc()
        end_ts_main = ts(start_time + timedelta(seconds=main_seconds))
        end_ts_final = ts(start_time + timedelta(seconds=main_seconds + extra_seconds))

        embed = build_timer_embed(
            vc_name=voice_channel.name, phase="running",
            main_total=main_seconds, extra_total=extra_seconds,
            remaining_main=main_seconds, remaining_total=main_seconds + extra_seconds,
            end_ts_main=end_ts_main, end_ts_final=end_ts_final,
            win_and_in=win_and_in, title_prefix="(DEV) " if IS_DEV else "",
        )
        sent = await ctx.followup.send(embed=embed)
        self.timer_messages[timer_id] = (sent.channel.id, sent.id)

        draw_event = asyncio.Event()
        self.active_timers[timer_id] = {
            "start_time": start_time,
            "durations": {"main": main_seconds, "easter_egg": egg_delay, "extra": extra_seconds},
            "original_durations": {"main": main_seconds, "extra": extra_seconds},
            "ctx": ctx,
            "voice_channel_id": vc_id,
            "vc_name": voice_channel.name,
            "win_and_in": win_and_in,
            "audio": {"turns": TURNS_AUDIO, "final": FINAL_AUDIO, "easter_egg": EASTER_EGG_AUDIO},
            "phase_override": None,
            "draw_event": draw_event,
        }

        # 4 tasks: embed loop + 3 audio-only
        self.timer_tasks[timer_id].append(asyncio.create_task(
            self._embed_update_loop(timer_id, voice_channel.name)))
        self.timer_tasks[timer_id].append(asyncio.create_task(
            self._audio_at(egg_delay, EASTER_EGG_AUDIO, timer_id, vc_id)))
        self.timer_tasks[timer_id].append(asyncio.create_task(
            self._audio_at(main_seconds, TURNS_AUDIO, timer_id, vc_id)))
        self.timer_tasks[timer_id].append(asyncio.create_task(
            self._final_audio(main_seconds + extra_seconds, FINAL_AUDIO, timer_id, vc_id, draw_event)))

        print(f"[timer] Scheduled {len(self.timer_tasks[timer_id])} tasks for timer_id={timer_id}")

        # intro audio (plays while the embed loop already runs)
        await self._play(ctx.guild, INTRO_AUDIO, channel_id=vc_id, leave_after=True)

    # ---------------- commands ----------------

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
        rand_val = random.random()
        print(
            f"[timer] Called by user={ctx.author.id}, guild={ctx.guild.id}, "
            f"voice_channel={voice_channel.id}, rand_val={rand_val}"
        )

        try:
            if rand_val <= FINALS_GAME_PROBABILITY:
                print("[timer] Branch: FINALS (no timer)")
                await self._send_finals(ctx, voice_channel)
                return

            win_and_in = rand_val <= (FINALS_GAME_PROBABILITY + SWISS_HAVE_TO_WIN_PROBABILITY)
            print(f"[timer] Branch: {'WIN & IN' if win_and_in else 'Regular swiss'}")
            await self._start_timed(ctx, voice_channel, win_and_in=win_and_in)
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
        remaining_main = max(durations["main"] - elapsed, 0.0)
        remaining_total = max(durations["main"] + durations["extra"] - elapsed, 0.0)
        remaining = {
            "main": remaining_main,
            "easter_egg": max(durations["easter_egg"] - elapsed, 0.0),
            "extra": remaining_total - remaining_main,
        }
        print(f"[pausetimer] elapsed={elapsed}, remaining={remaining}")

        # delete the live timer message
        ch_id, m_id = self.timer_messages.get(timer_id, (None, None))
        if ch_id and m_id:
            with contextlib.suppress(Exception):
                ch = self.bot.get_channel(ch_id)
                if ch:
                    orig = await ch.fetch_message(m_id)
                    await orig.delete()

        orig_durations = timer_data.get("original_durations") or durations
        embed = build_timer_embed(
            vc_name=timer_data["vc_name"], phase="paused",
            main_total=orig_durations["main"], extra_total=orig_durations["extra"],
            remaining_main=remaining_main, remaining_total=remaining_total,
            end_ts_main=0, end_ts_final=0,
            win_and_in=timer_data.get("win_and_in", False),
            title_prefix="(DEV) " if IS_DEV else "",
        )
        pause_msg = await ctx.followup.send(embed=embed)

        with contextlib.suppress(Exception):
            await ctx.interaction.delete_original_response()

        self.paused_timers[timer_id] = {
            "ctx": timer_data["ctx"],
            "remaining": remaining,
            "original_durations": orig_durations,
            "audio": timer_data["audio"],
            "pause_message": pause_msg,
            "voice_channel_id": timer_data.get("voice_channel_id"),
            "vc_name": timer_data["vc_name"],
            "win_and_in": timer_data.get("win_and_in", False),
        }
        self.timer_messages[timer_id] = (pause_msg.channel.id, pause_msg.id)

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

        orig_durations = paused.get("original_durations") or {
            "main": TIMER_MINUTES * 60.0, "extra": EXTRA_TURNS_MINUTES * 60.0,
        }
        main = paused["remaining"]["main"]
        egg = paused["remaining"]["easter_egg"]
        extra = paused["remaining"]["extra"]
        total_remaining = main + extra

        draw_event = asyncio.Event()
        phase = "running" if main > 0 else "extra"
        start_time = now_utc()
        end_ts_main = ts(start_time + timedelta(seconds=main))
        end_ts_final = ts(start_time + timedelta(seconds=total_remaining))

        vc_name = paused["vc_name"]
        embed = build_timer_embed(
            vc_name=vc_name, phase=phase,
            main_total=orig_durations["main"], extra_total=orig_durations["extra"],
            remaining_main=main, remaining_total=total_remaining,
            end_ts_main=end_ts_main, end_ts_final=end_ts_final,
            win_and_in=paused["win_and_in"], title_prefix="(DEV) " if IS_DEV else "",
        )
        msg = await ctx.followup.send(embed=embed)
        self.timer_messages[timer_id] = (msg.channel.id, msg.id)

        vcid = paused.get("voice_channel_id")
        self.active_timers[timer_id] = {
            "start_time": start_time,
            "durations": {"main": main, "easter_egg": egg, "extra": extra},
            "original_durations": orig_durations,
            "ctx": paused["ctx"],
            "voice_channel_id": vcid,
            "vc_name": vc_name,
            "win_and_in": paused["win_and_in"],
            "audio": paused["audio"],
            "phase_override": None,
            "draw_event": draw_event,
        }
        self.timer_tasks[timer_id] = []

        self.timer_tasks[timer_id].append(asyncio.create_task(
            self._embed_update_loop(timer_id, vc_name)))
        if egg > 0:
            self.timer_tasks[timer_id].append(asyncio.create_task(
                self._audio_at(egg, paused["audio"]["easter_egg"], timer_id, vcid)))
        if main > 0:
            self.timer_tasks[timer_id].append(asyncio.create_task(
                self._audio_at(main, paused["audio"]["turns"], timer_id, vcid)))
        self.timer_tasks[timer_id].append(asyncio.create_task(
            self._final_audio(total_remaining, paused["audio"]["final"], timer_id, vcid, draw_event)))

        print(f"[resumetimer] Rescheduled {len(self.timer_tasks[timer_id])} tasks for {timer_id}")


def setup(bot: commands.Bot):
    bot.add_cog(TimerCog(bot))
