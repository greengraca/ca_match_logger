import discord
from discord.ext import commands
import random
import asyncio
import datetime
from datetime import datetime, timezone, timedelta


class TimerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_timers = {}   # timer_id: {start_time, durations, messages, etc.}
        self.paused_timers = {}   # timer_id: {remaining_times, context, etc.}
        # Stores voice channel ID and a sequence number for timers
        self.voice_channel_timers = {}
        self.voice_channel_users = {}
        self.timer_messages = {}  # Keep track of timer messages
        self.timer_tasks = {}  # NEW: Store tasks for each timer to be able to cancel them


    async def timer_end(self, ctx, minutes, message, voice_file_path=None, timer_id=None, edit=False, delete_after=None):
        await asyncio.sleep(minutes * 60)  # Convert minutes to seconds
        
        channel = self.bot.get_channel(ctx.channel.id)
        if channel and timer_id in self.timer_messages and edit:
            try:
                msg = await ctx.channel.fetch_message(self.timer_messages[timer_id])
                await msg.edit(content=message)
            except Exception as e:
                print(f"Failed to edit message: {e}")
        elif channel:
            sent_msg = await channel.send(message)
            if timer_id:
                self.timer_messages[timer_id] = (ctx.channel.id, sent_msg.id)
                # self.timer_messages[timer_id] = sent_msg.id
                print(f"Stored message reference for timer {timer_id}: {self.timer_messages[timer_id]}")

        
        if voice_file_path and ctx.author.voice and ctx.author.voice.channel:
            vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)

            if vc and vc.is_connected():
                print("[DEBUG] Reusing VC for timer_end playback.")
            else:
                try:
                    vc = await ctx.author.voice.channel.connect(reconnect=False)
                    print("[DEBUG] Connected VC in timer_end.")
                except Exception as e:
                    await ctx.followup.send(f"❌ Could not connect to VC: {e}")
                    print(f"[ERROR] VC connection in timer_end failed: {e}")
                    return

            try:
                vc.play(discord.FFmpegPCMAudio(voice_file_path))
                while vc.is_playing():
                    await asyncio.sleep(1)
            finally:
                if vc.is_connected():
                    await vc.disconnect()

                    
        # After sending or editing the message
        if delete_after is not None:
            # If editing the message, ensure you have the message object
            if edit and timer_id in self.timer_messages:
                msg = await channel.fetch_message(self.timer_messages[timer_id])
            elif not edit:
                # Reuse the previously sent message if already stored
                if timer_id in self.timer_messages:
                    channel_id, message_id = self.timer_messages[timer_id]
                    msg = await channel.fetch_message(message_id)
                else:
                    msg = await channel.send(message)
                    if timer_id:
                        self.timer_messages[timer_id] = (ctx.channel.id, msg.id)

            # Schedule deletion of the message
            await asyncio.sleep(delete_after * 60)  # Convert minutes to seconds
            try:
                await msg.delete()
            except Exception as e:
                print(f"Failed to delete message: {e}")

    # Function to delete the message after 2 hours and 1 minute
    async def delete_message_later(self, message, delay):
        await asyncio.sleep(delay)  # delay in seconds
        try:
            await message.delete()
        except Exception as e:
            print(f"Error deleting message: {e}")    
            
            
    async def play_voice_file(self, ctx, voice_file_path, delay):
        """Plays a voice file in the user's current voice channel after a delay."""
        await asyncio.sleep(delay)

        # Reuse existing voice client if possible
        vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)

        if vc and vc.is_connected():
            print(f"[DEBUG] Reusing existing VC for playback.")
        else:
            try:
                vc = await ctx.author.voice.channel.connect(reconnect=False)
                print(f"[DEBUG] Connected for delayed playback.")
            except Exception as e:
                print(f"[ERROR] Could not connect to VC for delayed playback: {e}")
                return

        try:
            vc.play(discord.FFmpegPCMAudio(voice_file_path))
            while vc.is_playing():
                await asyncio.sleep(1)
        except Exception as e:
            print(f"[ERROR] Playback failed: {e}")
        finally:
            if vc.is_connected():
                await vc.disconnect()

    # @commands.slash_command(guild_ids=[690232443718074471], name="testvc", description="Test voice channel connection.")
    # async def testvc(self, ctx):
    #     await ctx.respond("Testing voice...")
    #     try:
    #         existing = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
    #         if existing:
    #             await existing.disconnect(force=True)

    #         vc = await ctx.author.voice.channel.connect(reconnect=False)
    #         await ctx.send("✅ Connected to voice!")
    #         await asyncio.sleep(5)
    #         await vc.disconnect()
    #     except Exception as e:
    #         await ctx.send(f"❌ Failed to connect: {e}")


    @commands.slash_command(guild_ids=[690232443718074471], name="timer", description="Sets a timer for a game.")
    async def timer(self, ctx):
        await ctx.defer()
        # Check if the user is in a voice channel
        if ctx.author.voice and ctx.author.voice.channel:
            voice_channel = ctx.author.voice.channel
            permissions = voice_channel.permissions_for(ctx.guild.me)

            # 🔍 Debug logs
            print(f"[DEBUG] Voice channel name: {voice_channel.name}")
            print(f"[DEBUG] Voice channel ID: {voice_channel.id}")
            print(f"[DEBUG] Guild ID: {ctx.guild.id}")
            print(f"[DEBUG] Permissions: connect={permissions.connect}, speak={permissions.speak}")

          
            try:
                existing_vc = discord.utils.get(self.bot.voice_clients, guild=ctx.guild)
                if existing_vc:
                    await existing_vc.disconnect(force=True)
                    print("[DEBUG] Force-disconnected existing VC.")
            except Exception as e:
                print(f"[ERROR] Failed to force disconnect existing VC: {e}")


            # Connect to voice channel
            try:
                vc = await ctx.author.voice.channel.connect(reconnect=True, timeout=10.0)
            except Exception as e:
                await ctx.followup.send("❌ Voice connection failed.")
                print(f"[ERROR] Voice connection failed: {e}")
                return


            if not vc or not vc.is_connected():
                await ctx.send("Failed to connect to the voice channel.")
                print("[ERROR] Voice client is None or not connected.")
                return

            print(f"[DEBUG] VoiceClient: connected={vc.is_connected()}, playing={vc.is_playing()}")
            
            voice_channel_id = voice_channel.id

            minutes = 80
            extra_time_for_turns = 30
            finals_game_probability = 0.15
            swiss_have_to_win_probability = 0.35
            
            rand_val = random.random()

            if rand_val <= finals_game_probability:
                # Finals Game Logic
                end_2hours_time = datetime.now(timezone.utc) + timedelta(minutes=150)
                end_2hours_timestamp = int(end_2hours_time.timestamp())
                sent_message = await ctx.followup.send(
                    "This is a final game with no time limit! You may ID and restart the match in the same positions if you all have time but in the end it has to have a winner, play accordingly."
                )
                asyncio.create_task(self.delete_message_later(sent_message, 7260))
                final_tts_path = "./timer/final.mp3"
                try:
                    vc.play(discord.FFmpegPCMAudio(final_tts_path))
                    print(f"[DEBUG] Playing audio: {final_tts_path}")
                except Exception as e:
                    print("[ERROR] Failed to play audio:")
                    # traceback.print_exc()
                    await vc.disconnect(force=True)
                    return
                # vc.play(discord.FFmpegPCMAudio(final_tts_path))
                while vc.is_playing():
                    await asyncio.sleep(1)
                await vc.disconnect(force=True)
                print("[DEBUG] Disconnected from voice.")


            elif rand_val <= finals_game_probability + swiss_have_to_win_probability:
                # Swiss-Have-To-Win Logic (same as normal game, different assets)
                minutes = 80
                extra_time_for_turns = 30
                end_time = datetime.now(timezone.utc) + timedelta(minutes=minutes)
                end_timestamp = int(end_time.timestamp())

                sent_message = await ctx.followup.send(
                    f"WIN & IN: Timer will start now and end <t:{end_timestamp}:R>. You have to win to make the final cut!"
                )
                     

                voice_channel_id = ctx.author.voice.channel.id
                self.voice_channel_timers[voice_channel_id] = self.voice_channel_timers.get(voice_channel_id, 0) + 1
                timer_id = f"{voice_channel_id}_{self.voice_channel_timers[voice_channel_id]}"
                self.voice_channel_users[timer_id] = [str(member.id) for member in ctx.author.voice.channel.members]
                # self.timer_messages[timer_id] = sent_message
                self.timer_messages[timer_id] = (sent_message.channel.id, sent_message.id)


                
                turns_end_time = datetime.now(timezone.utc) + timedelta(minutes=extra_time_for_turns)
                turns_end_timestamp = int(turns_end_time.timestamp())
                turns_message = f"Time is over. The active player should finish his turn and take a maximum of 30 minutes to do so - <t:{turns_end_timestamp}:R>."
                turns_file_path = "./timer/ap30minutes.mp3"
                final_turns_message = "If no one won until now, the game is a draw. Well Played."
                final_turns_file_path = "./timer/ggboyz.mp3"
                easter_egg_path = "./timer/brasileira10novo.mp3"
                
                self.active_timers[timer_id] = {
                    "start_time": datetime.now(timezone.utc),
                    "durations": {
                        "main": minutes * 60,
                        "easter_egg": (minutes - 10) * 60,
                        "extra": extra_time_for_turns * 60,
                    },
                    "ctx": ctx,
                    "messages": {
                        "turns": turns_message,
                        "final": final_turns_message,
                        "win_and_in": True  # ✅ Needed so /pausetimer and /resumetimer work correctly
                    },
                    "audio": {
                        "turns": turns_file_path,
                        "final": final_turns_file_path,
                        "easter_egg": easter_egg_path
                    }
                }                

                swiss_tts_path = "./timer/swiss80.mp3"
                try:
                    vc.play(discord.FFmpegPCMAudio(swiss_tts_path))
                    print(f"[DEBUG] Playing audio: {swiss_tts_path}")
                except Exception as e:
                    print("[ERROR] Failed to play audio:")
                    # traceback.print_exc()
                    await vc.disconnect(force=True)
                    return
                
                while vc.is_playing():
                    await asyncio.sleep(1)
                    
                await vc.disconnect(force=True)
                print("[DEBUG] Disconnected from voice.")




                if timer_id not in self.timer_tasks:
                    self.timer_tasks[timer_id] = []

                turns_task = asyncio.create_task(self.timer_end(ctx, minutes, turns_message, turns_file_path, timer_id=timer_id, edit=True))
                self.timer_tasks[timer_id].append(turns_task)
                easter_egg_task = asyncio.create_task(self.play_voice_file(ctx, easter_egg_path, (minutes - 10) * 60))
                self.timer_tasks[timer_id].append(easter_egg_task)
                final_turns_task = asyncio.create_task(self.timer_end(ctx, minutes + extra_time_for_turns, final_turns_message, final_turns_file_path, timer_id=timer_id, edit=True, delete_after=1))
                self.timer_tasks[timer_id].append(final_turns_task)
                    
                
            else:
                end_time = datetime.now(timezone.utc) + timedelta(minutes=minutes)
                end_timestamp = int(end_time.timestamp())
                sent_message = await ctx.followup.send(f"Timer will start now and end <t:{end_timestamp}:R>. Play to win and to your outs.")
                # Store this message ID immediately for later editing
                
                
                # Creating a timer_id that combines voice channel ID and its current timer sequence number
                self.voice_channel_timers[voice_channel_id] = self.voice_channel_timers.get(voice_channel_id, 0) + 1
                timer_id = f"{voice_channel_id}_{self.voice_channel_timers[voice_channel_id]}"
                self.voice_channel_users[timer_id] = [str(member.id) for member in voice_channel.members]

                
                self.timer_messages[timer_id] = (ctx.channel.id, sent_message.id)
                print(f"Timer started with ID {timer_id}. Users in channel: {self.voice_channel_users[timer_id]}")
                
                # Schedule the tasks for follow-up actions
                turns_end_time = datetime.now(timezone.utc) + timedelta(minutes=extra_time_for_turns)
                turns_end_timestamp = int(turns_end_time.timestamp())
                # turns_message = f"Turns have now started. You have 1 turn each after the active player's turn for a maximum of 20 minutes - <t:{turns_end_timestamp}:R>."
                turns_message = f"Time is over. The active player should finish his turn and take a maximum of 30 minutes to do so - <t:{turns_end_timestamp}:R>."
                turns_file_path = "./timer/ap30minutes.mp3"
                final_turns_message = "If no one won until now, the game is a draw. Well Played."
                final_turns_file_path = "./timer/ggboyz.mp3"
                
                easter_egg_path ="./timer/brasileira10novo.mp3"
                
                self.active_timers[timer_id] = {
                    "start_time": datetime.now(timezone.utc),
                    "durations": {
                        "main": minutes * 60,
                        "easter_egg": (minutes - 10) * 60,
                        "extra": extra_time_for_turns * 60,
                    },
                    "ctx": ctx,
                    "messages": {
                        "turns": turns_message,
                        "final": final_turns_message
                    },
                    "audio": {
                        "turns": turns_file_path,
                        "final": final_turns_file_path,
                        "easter_egg": easter_egg_path
                    }
                }

                # Start the timer and respond
                timer_tts_path = "./timer/timer80.mp3"
                
                try:
                    vc.play(discord.FFmpegPCMAudio(timer_tts_path))
                    print(f"[DEBUG] Playing audio: {timer_tts_path}")
                except Exception as e:
                    print("[ERROR] Failed to play audio:")
                    # traceback.print_exc()
                    await vc.disconnect(force=True)
                    return
                while vc.is_playing():
                    await asyncio.sleep(1)
                await vc.disconnect(force=True)

                
                # Creating tasks and storing them
                if timer_id not in self.timer_tasks:
                    self.timer_tasks[timer_id] = []
                    
                turns_task = asyncio.create_task(self.timer_end(ctx, minutes, turns_message, turns_file_path, timer_id=timer_id, edit=True))
                self.timer_tasks[timer_id].append(turns_task)

                easter_egg_task = asyncio.create_task(self.play_voice_file(ctx, easter_egg_path, (minutes - 10) * 60))
                self.timer_tasks[timer_id].append(easter_egg_task)

                final_turns_task = asyncio.create_task(self.timer_end(ctx, minutes + extra_time_for_turns, final_turns_message, final_turns_file_path, timer_id=timer_id, edit=True, delete_after=1))
                self.timer_tasks[timer_id].append(final_turns_task)    
                

        else:
            await ctx.respond("You need to be in a voice channel to use this command!", ephemeral=True)
            
            
    @commands.slash_command(guild_ids=[690232443718074471], name="endtimer", description="Manually ends the active timer.")
    async def endtimer(self, ctx):
        await ctx.defer()
        voice_channel_id = ctx.author.voice.channel.id if ctx.author.voice else None
        if not voice_channel_id:
            await ctx.respond("You're not in a voice channel.", ephemeral=True)
            return

        timer_id = f"{voice_channel_id}_{self.voice_channel_timers.get(voice_channel_id, 0)}"
        if self.is_user_in_timer(ctx.author.id, timer_id):
            await self.set_timer_stopped(timer_id, reason="endtimer")
            msg = await ctx.respond("Timer manually ended.")
            await asyncio.sleep(5)
            await msg.delete()
        else:
            await ctx.respond("You're not part of the current timer.", ephemeral=True)
            

            
    @commands.slash_command(guild_ids=[690232443718074471], name="pausetimer", description="Pauses the current timer.")
    async def pausetimer(self, ctx):
        await ctx.defer()
        if not ctx.author.voice:
            await ctx.followup.send("You're not in a voice channel.", ephemeral=True)
            return
        
       


        voice_channel_id = ctx.author.voice.channel.id
        timer_id = f"{voice_channel_id}_{self.voice_channel_timers.get(voice_channel_id, 0)}"
        # timer_id = self.get_timer_id_by_voice_channel_and_user(voice_channel_id, ctx.author.id)
        
        msg_data = self.timer_messages.get(timer_id)
        if not isinstance(msg_data, tuple):
            print(f"[ERROR] timer_messages[{timer_id}] is not a (channel_id, message_id) tuple: {msg_data}")
            return
        
        if not timer_id:
            await ctx.followup.send("❌ No active timer found for your voice channel.", ephemeral=True)
            return



        if not self.is_user_in_timer(ctx.author.id, timer_id):
            await ctx.followup.send("You're not part of the current timer.", ephemeral=True)
            return

        if timer_id not in self.active_timers:
            await ctx.followup.send("There's no active timer to pause.", ephemeral=True)
            return

        timer_data = self.active_timers.pop(timer_id)
        start_time = timer_data["start_time"]
        now = datetime.now(timezone.utc)
        elapsed = (now - start_time).total_seconds()

        durations = timer_data["durations"]
        remaining = {
            "main": max(durations["main"] - elapsed, 0),
            "easter_egg": max(durations["easter_egg"] - elapsed, 0),
            "extra": max(durations["extra"] - elapsed + durations["main"], 0)
        }

        # Cancel running tasks
        for task in self.timer_tasks.get(timer_id, []):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.timer_tasks[timer_id] = []

        # Delete the original timer message (from stored channel + message ID)
        try:
            channel_id, message_id = self.timer_messages.get(timer_id, (None, None))

            if channel_id and message_id:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    original_message = await channel.fetch_message(message_id)
                    await original_message.delete()
        except Exception as e:
            print(f"Error deleting original timer message: {e}")

        # Send the pause message with remaining time to everyone
        remaining_minutes = int(remaining["main"] // 60)
        pause_msg = await ctx.channel.send(f"⏸️ Timer paused – **{remaining_minutes} minutes** remaining.")
        # Delete the original thinking message:
        try:
            await ctx.interaction.delete_original_response()
        except Exception as e:
            print(f"Failed to delete original response: {e}")

        # Save paused state
        self.paused_timers[timer_id] = {
            "ctx": ctx,
            "remaining": remaining,
            "messages": timer_data["messages"],
            "audio": timer_data["audio"],
            "pause_message": pause_msg,
            "win_and_in": timer_data["messages"].get("win_and_in", False)  # Carry over this flag
        }


    @commands.slash_command(guild_ids=[690232443718074471], name="resumetimer", description="Resumes a paused timer.")
    async def resumetimer(self, ctx):
        await ctx.defer()
        if not ctx.author.voice:
            await ctx.followup.send("You're not in a voice channel.", ephemeral=True)
            return

        voice_channel_id = ctx.author.voice.channel.id
        timer_id = f"{voice_channel_id}_{self.voice_channel_timers.get(voice_channel_id, 0)}"

        if timer_id not in self.paused_timers:
            await ctx.followup.send("No paused timer found for your voice channel.", ephemeral=True)
            return

        paused = self.paused_timers.pop(timer_id)

        # Delete the pause message
        pause_msg = paused.get("pause_message")
        if pause_msg:
            try:
                await pause_msg.delete()
            except Exception as e:
                print(f"Failed to delete pause message on resume for {timer_id}: {e}")

        # Restore active timer state
        self.active_timers[timer_id] = {
            "start_time": datetime.now(timezone.utc),
            "durations": paused["remaining"],
            "messages": paused["messages"],
            "audio": paused["audio"]
        }
        self.timer_tasks[timer_id] = []

        old_ctx = paused["ctx"]
        turns_msg = paused["messages"]["turns"]
        final_msg = paused["messages"]["final"]
        turns_audio = paused["audio"]["turns"]
        final_audio = paused["audio"]["final"]
        easter_egg_audio = paused["audio"]["easter_egg"]

        main = paused["remaining"]["main"]
        egg = paused["remaining"]["easter_egg"]
        extra = paused["remaining"]["extra"]

        turns_task = asyncio.create_task(self.timer_end(old_ctx, main / 60, turns_msg, turns_audio, timer_id=timer_id, edit=True))
        self.timer_tasks[timer_id].append(turns_task)

        easter_egg_task = asyncio.create_task(self.play_voice_file(old_ctx, easter_egg_audio, egg))
        self.timer_tasks[timer_id].append(easter_egg_task)

        final_task = asyncio.create_task(self.timer_end(old_ctx, extra / 60, final_msg, final_audio, timer_id=timer_id, edit=True, delete_after=1))
        self.timer_tasks[timer_id].append(final_task)

        # Determine the end time and message style
        end_time = datetime.now() + timedelta(seconds=main)
        end_timestamp = int(end_time.timestamp())

        is_win_and_in = paused.get("win_and_in", False)

        if is_win_and_in:
            resume_text = f"WIN & IN: Timer has been resumed and will end <t:{end_timestamp}:R>. You have to win to make the final cut!"
        else:
            resume_text = f"Timer has been resumed and will end <t:{end_timestamp}:R>. Play to win and to your outs."

        msg = await ctx.followup.send(resume_text)
        self.timer_messages[timer_id] = (msg.channel.id, msg.id)


  
    
    async def set_timer_stopped(self, timer_id, reason: str = "track"):
        """Mark a timer as stopped, cancel all related tasks, and edit the message to show the timer was stopped."""
        if timer_id in self.voice_channel_users:
            # Mark the timer as stopped
            self.voice_channel_users[timer_id] = "stopped"
            self.active_timers.pop(timer_id, None)
            self.paused_timers.pop(timer_id, None)

            # Cancel all tasks associated with the stopped timer
            tasks = self.timer_tasks.get(timer_id, [])
            for task in tasks:
                if not task.done():
                    task.cancel()
                    try:
                        await task  # This waits for the task cancellation to complete
                    except asyncio.CancelledError:
                        # Expected exception when a task is cancelled
                        pass
            # Clear the task list for this timer_id after cancellation
            self.timer_tasks[timer_id] = []
            
            # Build appropriate message
            reason_text = "due to /track command." if reason == "track" else "due to /endtimer command."

            # Edit the last message to indicate the timer was stopped
            if timer_id in self.timer_messages:
                channel_id, message_id = self.timer_messages[timer_id]
                async def delayed_delete(message, delay=60):
                        await asyncio.sleep(delay)
                        try:
                            await message.delete()
                        except Exception as e:
                            print(f"Failed to delete message: {e}")    
                channel = self.bot.get_channel(channel_id)  # Get channel separately
                if channel:
                    try:
                        msg = await channel.fetch_message(message_id)
                        await msg.edit(content=f"Timer was stopped {reason_text}")
                        print(f"Edited message for timer {timer_id} to indicate it was stopped.")
                        # Wait for 1 minute before deleting the message
                        asyncio.create_task(delayed_delete(msg, 60))
                        print(f"Deleted message for timer {timer_id} after indicating it was stopped.")
                    except Exception as e:
                        print(f"Failed to edit or delete message for timer {timer_id}: {e}")
                else:
                    print(f"Channel not found for timer {timer_id} message edit.")
                # Remove the message reference after editing and planning for deletion
                del self.timer_messages[timer_id]




    def is_user_in_timer(self, user_id, timer_id):
        """Check if a user is part of an active timer."""
        print(f"Checking if user {user_id} (type: {type(user_id)}) is in timer {timer_id}:")
        if timer_id in self.voice_channel_users:
            users_in_timer = self.voice_channel_users.get(timer_id, [])
            print(f"Users in timer {timer_id}: {users_in_timer} (types: {[type(u) for u in users_in_timer]})")
            is_in_timer = str(user_id) in [str(u) for u in users_in_timer]  # Ensure consistent type comparison
            print(f"Result of check: {is_in_timer}")
            return is_in_timer
        else:
            print(f"No active timer found with ID {timer_id}.")
            return False



def setup(bot):
    bot.add_cog(TimerCog(bot))