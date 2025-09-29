# cogs/admin.py
from typing import Annotated, List, Dict

import discord
from discord.ext import commands
from discord.commands import slash_command, Option
from rapidfuzz import fuzz, process

from config import GUILD_ID, IS_DEV
from db import decks, matches, individual_results, set_counter_to_max_match_id  
from utils.ephemeral import should_be_ephemeral
from utils.text import capitalize_words
from utils.perms import is_mod

import logging
log = logging.getLogger("ca_match_logger")


# ---------- helpers ----------

async def deck_autocomplete(ctx: discord.AutocompleteContext) -> List[str]:
    """All deck names from DB, filtered by substring."""
    cursor = decks.find({}, {"name": 1, "_id": 0})
    all_decks = [doc["name"] async for doc in cursor]
    q = (ctx.value or "").lower()
    return [d for d in all_decks if q in d.lower()][:25]


async def misnamed_deck_autocomplete(ctx: discord.AutocompleteContext) -> List[str]:
    """Names present in logs but missing from decks collection (case-insensitive)."""
    valid = await decks.distinct("name")
    valid_lower = {v.lower() for v in valid if v}

    ir_names = await individual_results.distinct("deck_name")
    m_names = await matches.distinct("players.deck_name")
    logged = {n for n in (ir_names + m_names) if isinstance(n, str) and n.strip()}

    missing = [n for n in logged if n.lower() not in valid_lower]
    q = (ctx.value or "").lower()
    return [n for n in missing if q in n.lower()][:25]


async def case_insensitive_doc(coll, field: str, value: str):
    return await coll.find_one({field: {"$regex": f"^{value}$", "$options": "i"}})


async def recompute_deck_players_for(decks_coll, ir_coll, deck_names: List[str]):
    for dn in set(deck_names):
        dn_stripped = (dn or "").strip()
        if not dn_stripped:
            continue

        stats: Dict[int, Dict[str, int]] = {}
        total_ir = 0

        cursor = ir_coll.find({"deck_name": {"$regex": f"^{dn_stripped}$", "$options": "i"}})
        async for r in cursor:
            total_ir += 1
            try:
                pid = int(r["player_id"])
            except Exception:
                pid = int(str(r.get("player_id", "0")))
            res = (r.get("result") or "").lower()
            s = stats.setdefault(pid, {"wins": 0, "losses": 0, "draws": 0})
            if res == "win":
                s["wins"] += 1
            elif res == "loss":
                s["losses"] += 1
            elif res == "draw":
                s["draws"] += 1
            else:
                log.warning("recompute: deck='%s' unknown result '%s' in IR doc=%s",
                            dn_stripped, r.get("result"), r.get("_id"))


        # # per-player breakdown
        # for pid, vals in stats.items():
        #     log.info("recompute: deck='%s' pid=%s W=%s L=%s D=%s",
        #              dn_stripped, pid, vals["wins"], vals["losses"], vals["draws"])

        players_list = [{"player_id": pid, **vals} for pid, vals in stats.items()]
        upd = await decks_coll.update_one(
            {"name": {"$regex": f"^{dn_stripped}$", "$options": "i"}},
            {"$set": {"players": players_list}},
        )


async def get_top_decks_for_player(player_id: int, limit: int = 5) -> List[str]:
    """Return most-used deck names for a player from individual_results."""
    pipeline = [
        {"$match": {"player_id": {"$in": [player_id, str(player_id)]}}},
        {"$group": {"_id": "$deck_name", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    top = []
    async for row in individual_results.aggregate(pipeline):
        if isinstance(row.get("_id"), str) and row["_id"].strip():
            top.append(row["_id"])
    return top

def _collect_proposed_results(m: dict, edits: Dict[int, Dict]) -> list[str]:
    """Return final results per player (index 0..3) after applying queued edits."""
    results = []
    for i, p in enumerate(m.get("players", [])[:4]):
        new_res = (edits.get(i, {}).get("result") or p.get("result") or "").strip().lower()
        results.append(new_res)
    return results

def _results_valid(final_results: list[str]) -> bool:
    """True if: exactly one win & no draws, rest loss  OR  all draw."""
    # normalize and ensure only allowed tokens
    allowed = {"win", "loss", "draw"}
    if not all(r in allowed for r in final_results):
        return False
    n = len(final_results)
    wins = final_results.count("win")
    draws = final_results.count("draw")
    losses = final_results.count("loss")
    # all draw
    if draws == n:
        return True
    # exactly one winner, rest losses
    if wins == 1 and draws == 0 and losses == n - 1:
        return True
    return False


# ---------- interactive UI for /edittrack ----------

class EditButton(discord.ui.Button):
    def __init__(self, idx: int, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=idx // 3)
        self.idx = idx

    async def callback(self, interaction: discord.Interaction):
        view: "EditTrackView" = self.view  # type: ignore
        cur = view.match["players"][self.idx]
        panel = await EditPlayerPanel.create(view, self.idx, cur)

        # First response to this interaction MUST be via response.*, not followup
        await interaction.response.send_message(
            f"Editing **P{self.idx+1}** — pick fields to change, then press **Apply Changes**.",
            view=panel,
            ephemeral=True,
        )

        # Grab the message handle so we can later edit/delete it
        msg = await interaction.original_response()
        view.temp_msgs.append(msg)




class ApplyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✅ Apply Changes", style=discord.ButtonStyle.success, row=3)

    async def callback(self, interaction: discord.Interaction):
        view: "EditTrackView" = self.view  # type: ignore
        await interaction.response.defer()
        # cleanup ephemeral helper messages
        for m in list(view.temp_msgs):
            try:
                await m.delete()
            except Exception:
                pass
        view.temp_msgs.clear()
        view.stop()
        try:
            await interaction.edit_original_response(view=None)
        except Exception:
            pass


class CancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="❌ Cancel", style=discord.ButtonStyle.danger, row=3)

    async def callback(self, interaction: discord.Interaction):
        view: "EditTrackView" = self.view  # type: ignore
        view.edits.clear()
        await interaction.response.defer()
        # cleanup ephemeral helper messages
        for m in list(view.temp_msgs):
            try:
                await m.delete()
            except Exception:
                pass
        view.temp_msgs.clear()
        view.stop()
        try:
            await interaction.edit_original_response(content="Edit cancelled.", view=None)
        except Exception:
            pass


class EditTrackView(discord.ui.View):
    def __init__(self, author_id: int, match_doc: dict, *, timeout: float = 300.0):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.match = match_doc
        self.edits: Dict[int, Dict] = {}
        self.temp_msgs: list[discord.WebhookMessage] = []  # <-- add this


        # one button per player (up to 4)
        for i, p in enumerate(self.match.get("players", [])[:4]):
            label = f"Edit P{i+1} ({p.get('deck_name', '?')})"
            self.add_item(EditButton(i, label))

        self.add_item(ApplyButton())
        self.add_item(CancelButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This prompt isn't for you 👀", ephemeral=True)
            return False
        return True
    


class ResultSelect(discord.ui.Select):
    def __init__(self, parent_view: "EditTrackView", idx: int, current: dict):
        options = [
            discord.SelectOption(label="win", value="win", default=current.get("result") == "win"),
            discord.SelectOption(label="loss", value="loss", default=current.get("result") == "loss"),
            discord.SelectOption(label="draw", value="draw", default=current.get("result") == "draw"),
        ]
        super().__init__(placeholder="Set result…", min_values=1, max_values=1, options=options, row=0)
        self.parent_view = parent_view
        self.idx = idx
        
    async def callback(self, interaction: discord.Interaction):
        res = self.values[0]
        cur = self.parent_view.edits.get(self.idx, {})
        cur["result"] = res
        self.parent_view.edits[self.idx] = cur
        # Replace the panel message itself
        await interaction.response.edit_message(
            content=f"Queued **result** → `{res}` for P{self.idx+1}.",
            view=None
        )


class SeatSelect(discord.ui.Select):
    def __init__(self, parent_view: "EditTrackView", idx: int, current: dict):
        seat = current.get("position")
        options = [
            discord.SelectOption(label=str(n), value=str(n), default=str(seat) == str(n))
            for n in (1, 2, 3, 4)
        ]
        super().__init__(placeholder="Set seat…", min_values=1, max_values=1, options=options, row=1)
        self.parent_view = parent_view
        self.idx = idx

    async def callback(self, interaction: discord.Interaction):
        pos_val = int(self.values[0])
        cur = self.parent_view.edits.get(self.idx, {})
        cur["position"] = pos_val
        self.parent_view.edits[self.idx] = cur
        await interaction.response.edit_message(
            content=f"Queued **seat** → `{pos_val}` for P{self.idx+1}.",
            view=None
        )
        

class ConfirmDeckChangeView(discord.ui.View):
    def __init__(self, parent_view: "EditTrackView", idx: int, old_deck: str, new_deck: str):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        self.idx = idx
        self.old_deck = old_deck
        self.new_deck = new_deck
        self.add_item(PanelCancelButton(row=1))

    @discord.ui.button(label="Yes — change deck", style=discord.ButtonStyle.success, row=0)
    async def yes(self, _: discord.ui.Button, interaction: discord.Interaction):
        cur = self.parent_view.edits.get(self.idx, {})
        cur["deck_name"] = self.new_deck
        self.parent_view.edits[self.idx] = cur
        await interaction.response.edit_message(
            content=(f"✅ Queued **P{self.idx+1}** deck change: "
                    f"`{self.old_deck}` → **{self.new_deck}**.\n"
                    f"*Press **Apply Changes** to save to the database.*"),
            view=None
        )
        self.stop()


class DeckButton(discord.ui.Button):
    def __init__(self, label: str, parent_view: "EditTrackView", idx: int):
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=3)
        self.parent_view = parent_view
        self.idx = idx


    async def callback(self, interaction: discord.Interaction):
        cur = self.parent_view.match["players"][self.idx]
        old_deck = cur.get("deck_name", "?")
        new_deck = self.label.strip()
        await interaction.response.edit_message(
            content=(f"Change **P{self.idx+1}** deck from `{old_deck}` to **{new_deck}**?"),
            view=ConfirmDeckChangeView(self.parent_view, self.idx, old_deck, new_deck),
        )


class CustomDeckInputModal(discord.ui.Modal):
    def __init__(self, parent_view: "EditTrackView", idx: int):
        super().__init__(title=f"Custom Deck for P{idx+1}")
        self.parent_view = parent_view
        self.idx = idx
        self.deck = discord.ui.InputText(label="Deck Name", placeholder="Exact deck name…", required=True)
        self.add_item(self.deck)
        
    async def callback(self, interaction: discord.Interaction):
        deck_name = self.deck.value.strip()
        cur = self.parent_view.match["players"][self.idx]
        old_deck = cur.get("deck_name", "?")

        await interaction.response.send_message(
            content=(f"Change **P{self.idx+1}** deck from `{old_deck}` to **{deck_name}**?"),
            view=ConfirmDeckChangeView(self.parent_view, self.idx, old_deck, deck_name),
            ephemeral=True,
        )
        msg = await interaction.original_response()   # <-- grab handle
        self.parent_view.temp_msgs.append(msg)


class CustomDeckButton(discord.ui.Button):
    def __init__(self, parent_view: "EditTrackView", idx: int):
        super().__init__(label="Search deck (autocomplete)", style=discord.ButtonStyle.secondary, row=4)
        self.parent_view = parent_view
        self.idx = idx

    async def callback(self, interaction: discord.Interaction):
        match_id = self.parent_view.match["match_id"]
        pnum = self.idx + 1

        # Close this panel message
        await interaction.response.edit_message(
            content="Editor closed. Use the slash command below.",
            view=None
        )

        # Clean up any other helper messages from this editor
        for m in list(self.parent_view.temp_msgs):
            try:
                await m.delete()
            except Exception:
                pass
        self.parent_view.temp_msgs.clear()

        # Stop the main editor view (so buttons no longer respond)
        self.parent_view.stop()
        
        try:
            # deletes the original editor message (the one with the 4 Edit buttons + Apply/Cancel)
            await interaction.delete_original_response()
        except Exception:
            pass


        # Give the user the exact command to run
        await interaction.followup.send(
            f"➡️ Use `/setplayerdeck` with:\n"
            f"• **match_id**: `{match_id}`\n"
            f"• **player**: `{pnum}`\n"
            f"• **deck**: *(autocomplete)*\n\n"
            f"After you set it, I’ll re-open the editor automatically.",
            ephemeral=True
        )


        
class PanelCancelButton(discord.ui.Button):
    def __init__(self, row: int = 4):
        super().__init__(label="⛔ Cancel", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Editor closed.", view=None)
        self.view.stop()

class ChangePlayerButton(discord.ui.Button):
    def __init__(self, parent_view: "EditTrackView", idx: int):
        super().__init__(label="Change Player (search)", style=discord.ButtonStyle.secondary, row=2)
        self.parent_view = parent_view
        self.idx = idx

    async def callback(self, interaction: discord.Interaction):
        match_id = self.parent_view.match["match_id"]
        pnum = self.idx + 1

        # Close this panel message
        await interaction.response.edit_message(
            content="Editor closed. Use the slash command below.",
            view=None
        )

        # Clean up any other helper messages from this editor
        for m in list(self.parent_view.temp_msgs):
            try:
                await m.delete()
            except Exception:
                pass
        self.parent_view.temp_msgs.clear()

        # Stop the main editor view (so buttons no longer respond)
        self.parent_view.stop()
        try:
            await interaction.delete_original_response()  # remove the main "Editing Match..." message
        except Exception:
            pass

        await interaction.followup.send(
            f"➡️ Use `/setplayer` with:\n"
            f"• **match_id**: `{match_id}`\n"
            f"• **player**: `{pnum}`\n"
            f"• **new_player**: *(search by member)*\n\n"
            f"After you set it, I’ll re-open the editor automatically.",
            ephemeral=True
        )


class EditPlayerPanel(discord.ui.View):
    """Small panel to edit one player's fields without opening a modal automatically."""
    def __init__(self, parent_view: "EditTrackView", idx: int, current: dict, top_decks: List[str]):
        super().__init__(timeout=120)
        self.add_item(ResultSelect(parent_view, idx, current))  # row 0
        self.add_item(SeatSelect(parent_view, idx, current))    # row 1
        self.add_item(ChangePlayerButton(parent_view, idx))     # row 2
        for dn in top_decks[:5]:                                # row 3
            self.add_item(DeckButton(dn, parent_view, idx))
        self.add_item(CustomDeckButton(parent_view, idx))       # row 4
        self.add_item(PanelCancelButton(row=4))                 # row 4 (shares)

    @classmethod
    async def create(cls, parent_view: "EditTrackView", idx: int, current: dict):
        # figure out player_id (supports int/str)
        pid_raw = current.get("player_id")
        try:
            pid_int = int(pid_raw)
        except Exception:
            pid_int = None

        top = await get_top_decks_for_player(pid_int) if pid_int is not None else []
        return cls(parent_view, idx, current, top)
    

class DeleteTrackView(discord.ui.View):
    def __init__(self, author_id: int, match_doc: dict):
        super().__init__(timeout=90)
        self.author_id = author_id
        self.m = match_doc
        self._done = False

    def _same_person(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    def _summary_lines(self) -> list[str]:
        lines = []
        def _mention(pid): return f"<@{pid}>"
        for i, p in enumerate(self.m.get("players", [])[:4], start=1):
            raw_pos = p.get("position")
            try:
                seat = int(raw_pos)
            except (TypeError, ValueError):
                seat = "?"
            lines.append(
                f"**P{i}** • {_mention(p.get('player_id','?'))} • "
                f"Seat {seat if seat in {1,2,3,4} else '?'} • "
                f"Deck: *{p.get('deck_name','?')}* • Result: **{p.get('result','?')}**"
            )
        return lines

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="✅ Confirm delete", style=discord.ButtonStyle.danger)
    async def confirm(self, _, interaction: discord.Interaction):
        if not self._same_person(interaction):
            await interaction.response.send_message("Only the command invoker can confirm this.", ephemeral=True)
            return
        if self._done:
            await interaction.response.send_message("This action has already been processed.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # collect affected deck names before deletion
        affected = []
        for p in self.m.get("players", []):
            dn = (p.get("deck_name") or "").strip()
            if dn:
                affected.append(dn)

        # delete match + its individual_results
        mid = self.m["match_id"]
        await matches.delete_one({"match_id": mid})
        await individual_results.delete_many({"match_id": mid})

        # recompute players lists for affected decks
        # if affected:
        #     await recompute_deck_players_for(decks, individual_results, affected)
        #     await set_counter_to_max_match_id()
            
        try:
            if affected:
                await recompute_deck_players_for(decks, individual_results, list(set(affected)))
        except Exception as e:
            log.warning("recompute_deck_players_for failed after deletion: %s", e)
            
        await set_counter_to_max_match_id()



        # feedback
        emb = discord.Embed(
            title=f"🗑️ Match {mid} deleted",
            description="\n".join(self._summary_lines()) or "_No players?_",
            color=0xFF0000 if IS_DEV else 0x00FF00,
        )
        if affected:
            emb.add_field(name="Deck stats updated", value=", ".join(sorted(set(affected))), inline=False)

        self._disable_all()
        self._done = True
        await interaction.edit_original_response(embed=emb, view=self)

    @discord.ui.button(label="✖️ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, _, interaction: discord.Interaction):
        if not self._same_person(interaction):
            await interaction.response.send_message("Only the command invoker can cancel this.", ephemeral=True)
            return
        self._disable_all()
        emb = discord.Embed(
            title=f"❎ Deletion cancelled for match {self.m.get('match_id')}",
            description="\n".join(self._summary_lines()) or "_No players?_",
        )
        await interaction.response.edit_message(embed=emb, view=self)

    async def on_timeout(self):
        # best-effort: disable buttons if the message is still editable
        self._disable_all()


def _parse_match_id(inp: str) -> int | None:
    """Accepts a raw ID or a message link containing an ID-like number."""
    if not inp:
        return None
    # take the first integer group you find
    import re
    m = re.search(r"\d+", str(inp))
    try:
        return int(m.group(0)) if m else None
    except Exception:
        return None


# ---------- Cog ----------

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # /removedeckfromdatabase
    @slash_command(
        guild_ids=[GUILD_ID],
        name="removedeckfromdatabase",
        description="Remove a deck. Optionally transfer logs/stats to another deck. (Mods only)",
    )
    async def removedeckfromdatabase(
        self,
        ctx: discord.ApplicationContext,
        old_deck: Annotated[str, Option(str, "Deck to remove", autocomplete=deck_autocomplete)],
        new_deck: Annotated[str | None, Option(str, "Deck to transfer to", autocomplete=deck_autocomplete, required=False)] = None,
    ):
        eph = should_be_ephemeral(ctx)
        if not is_mod(ctx.author):
            await ctx.respond(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=0xFF0000), ephemeral=True)
            return

        await ctx.defer(ephemeral=eph)

        old_doc = await case_insensitive_doc(decks, "name", old_deck)
        if not old_doc:
            await ctx.followup.send(
                embed=discord.Embed(
                    title="Deck Not Found",
                    description=f"{capitalize_words(old_deck)} does not exist.",
                    color=0xFF0000 if IS_DEV else 0x00FF00),
                ephemeral=eph)
            return

        # no transfer target: block removal if there’s associated data
        if not new_deck:
            has_results = await individual_results.find_one(
                {"deck_name": {"$regex": f"^{old_deck}$", "$options": "i"}}
            )
            has_players = bool(old_doc.get("players"))
            if has_results or has_players:
                await ctx.followup.send(
                    embed=discord.Embed(
                        title="Cannot Remove Deck",
                        description=(f"{capitalize_words(old_deck)} has associated game logs or player data.\n"
                                     "Specify a deck to transfer these records before removal."),
                        color=0xFF0000),
                    ephemeral=eph)
                return

            # safe to delete
            await decks.delete_one({"_id": old_doc["_id"]})
            await ctx.followup.send(
                embed=discord.Embed(
                    title="Deck Removed",
                    description=f"{capitalize_words(old_deck)} was removed.",
                    color=0xFF0000 if IS_DEV else 0x00FF00),
                ephemeral=eph)
            return

        # transfer path
        new_doc = await case_insensitive_doc(decks, "name", new_deck)
        if not new_doc:
            await ctx.followup.send(
                embed=discord.Embed(
                    title="New Deck Not Found",
                    description=f"{capitalize_words(new_deck)} does not exist.",
                    color=0xFF0000),
                ephemeral=eph)
            return

        # 1) Move individual_results
        ir_res = await individual_results.update_many(
            {"deck_name": {"$regex": f"^{old_deck}$", "$options": "i"}},
            {"$set": {"deck_name": new_doc["name"]}}
        )

        # 2) Move matches player entries
        m_res = await matches.update_many(
            {"players.deck_name": {"$regex": f"^{old_deck}$", "$options": "i"}},
            {"$set": {"players.$[elem].deck_name": new_doc["name"]}},
            array_filters=[{"elem.deck_name": {"$regex": f"^{old_deck}$", "$options": "i"}}],
        )

        # 3) Merge players arrays into new deck
        merged: Dict[int, Dict[str, int]] = {}
        for p in (new_doc.get("players") or []):
            merged[p["player_id"]] = {
                "wins": p.get("wins", 0),
                "losses": p.get("losses", 0),
                "draws": p.get("draws", 0),
            }
        for p in (old_doc.get("players") or []):
            s = merged.setdefault(p["player_id"], {"wins": 0, "losses": 0, "draws": 0})
            s["wins"] += p.get("wins", 0)
            s["losses"] += p.get("losses", 0)
            s["draws"] += p.get("draws", 0)

        merged_list = [{"player_id": pid, **stats} for pid, stats in merged.items()]
        await decks.update_one({"_id": new_doc["_id"]}, {"$set": {"players": merged_list}})

        # 4) Remove old deck doc
        await decks.delete_one({"_id": old_doc["_id"]})

        await ctx.followup.send(
            embed=discord.Embed(
                title="Deck Removed + Logs Transferred",
                description=(f"{capitalize_words(old_deck)} was removed and all logs "
                             f"were transferred to {capitalize_words(new_doc['name'])}.\n"
                             f"Updated IR: **{ir_res.modified_count}**, Matches: **{m_res.modified_count}**"),
                color=0xFF0000 if IS_DEV else 0x00FF00),
            ephemeral=eph)

    # /findmisnameddecks
    @slash_command(
        guild_ids=[GUILD_ID],
        name="findmisnameddecks",
        description="Find deck names in logs that aren't in the decks collection. (Mods only)",
    )
    async def findmisnameddecks(self, ctx: discord.ApplicationContext):
        eph = should_be_ephemeral(ctx)
        if not is_mod(ctx.author):
            await ctx.respond(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=0xFF0000), ephemeral=True)
            return

        await ctx.defer(ephemeral=eph)

        existing = await decks.distinct("name")
        existing_set = set(n for n in existing if n)
        existing_lower = {n.lower() for n in existing_set}

        ir_names = await individual_results.distinct("deck_name")
        m_names = await matches.distinct("players.deck_name")
        logged = {n for n in (ir_names + m_names) if isinstance(n, str) and n.strip()}

        missing = sorted([n for n in logged if n.lower() not in existing_lower])

        if not missing:
            await ctx.followup.send(embed=discord.Embed(
                title="No Misnamed Decks Found",
                description="All logged decks exist in the database.",
                color=0xFF0000 if IS_DEV else 0x00FF00), ephemeral=eph)
            return

        # Suggest corrections
        embed = discord.Embed(title="Misnamed Decks Found", color=0xFF0000 if IS_DEV else 0x00FF00)
        for name in missing[:20]:  # cap fields to avoid embed overflow
            suggestions = [
                cand for cand, score, _ in process.extract(
                    name, list(existing_set), scorer=fuzz.token_set_ratio, limit=5
                ) if score >= 60 or fuzz.partial_ratio(name, cand) >= 60
            ]
            embed.add_field(name=name, value=(", ".join(suggestions) or "_No close matches_"), inline=False)

        await ctx.followup.send(embed=embed, ephemeral=eph)

    # /correctmisnameddecks
    @slash_command(
        guild_ids=[GUILD_ID],
        name="correctmisnameddecks",
        description="Correct a misnamed deck across logs and update deck stats. (Mods only)",
    )
    async def correctmisnameddecks(
        self,
        ctx: discord.ApplicationContext,
        misnamed_deck: Annotated[str, Option(str, "Misnamed deck", autocomplete=misnamed_deck_autocomplete)],
        correct_deck: Annotated[str, Option(str, "Correct deck", autocomplete=deck_autocomplete)],
    ):
        eph = should_be_ephemeral(ctx)
        if not is_mod(ctx.author):
            await ctx.respond(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=0xFF0000), ephemeral=True)
            return

        await ctx.defer(ephemeral=eph)

        # Ensure correct deck exists
        correct_doc = await case_insensitive_doc(decks, "name", correct_deck)
        if not correct_doc:
            await ctx.followup.send(embed=discord.Embed(
                title="Invalid Deck Name",
                description=f"'{correct_deck}' is not a valid deck name.",
                color=0xFF0000), ephemeral=eph)
            return

        # Update IR
        ir_res = await individual_results.update_many(
            {"deck_name": misnamed_deck},
            {"$set": {"deck_name": correct_doc["name"]}}
        )

        # Update Matches
        m_res = await matches.update_many(
            {"players.deck_name": misnamed_deck},
            {"$set": {"players.$[elem].deck_name": correct_doc["name"]}},
            array_filters=[{"elem.deck_name": misnamed_deck}],
        )

        # Recalculate deck.players from IR
        stats_acc: Dict[int, Dict[str, int]] = {}
        async for r in individual_results.find({"deck_name": correct_doc["name"]}):
            pid = int(r["player_id"])
            t = r["result"]
            s = stats_acc.setdefault(pid, {"wins": 0, "losses": 0, "draws": 0})
            if t == "win":
                s["wins"] += 1
            elif t == "loss":
                s["losses"] += 1
            elif t == "draw":
                s["draws"] += 1

        players_list = [{"player_id": pid, **vals} for pid, vals in stats_acc.items()]
        await decks.update_one({"_id": correct_doc["_id"]}, {"$set": {"players": players_list}})

        await ctx.followup.send(embed=discord.Embed(
            title="Deck Correction Successful",
            description=(f"Replaced **{misnamed_deck}** → **{correct_doc['name']}**.\n"
                         f"Updated IR: **{ir_res.modified_count}**, Matches: **{m_res.modified_count}**.\n"
                         f"Deck stats recalculated."),
            color=0xFF0000 if IS_DEV else 0x00FF00), ephemeral=eph)

    # /editdeckindatabase
    @slash_command(
        guild_ids=[GUILD_ID],
        name="editdeckindatabase",
        description="Rename a deck across database and logs. (Mods only)",
    )
    async def editdeckindatabase(
        self,
        ctx: discord.ApplicationContext,
        old_deck_name: Annotated[str, Option(str, "Current name", autocomplete=deck_autocomplete)],
        new_deck_name: Annotated[str, Option(str, "New name")],
    ):
        eph = should_be_ephemeral(ctx)
        if not is_mod(ctx.author):
            await ctx.respond(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=0xFF0000), ephemeral=True)
            return

        await ctx.defer(ephemeral=eph)

        # Reject if target already exists
        exists = await case_insensitive_doc(decks, "name", new_deck_name)
        if exists:
            await ctx.followup.send(embed=discord.Embed(
                title="Deck Name Already Exists",
                description=f"'{new_deck_name}' is already in use.",
                color=0xFF0000), ephemeral=eph)
            return

        # Update decks doc
        upd_decks = await decks.update_one(
            {"name": {"$regex": f"^{old_deck_name}$", "$options": "i"}},
            {"$set": {"name": new_deck_name}}
        )
        if upd_decks.matched_count == 0:
            await ctx.followup.send(embed=discord.Embed(
                title="Deck Not Found",
                description=f"'{old_deck_name}' was not found.",
                color=0xFF0000), ephemeral=eph)
            return

        # Update IR + Matches
        upd_ir = await individual_results.update_many(
            {"deck_name": {"$regex": f"^{old_deck_name}$", "$options": "i"}},
            {"$set": {"deck_name": new_deck_name}}
        )
        upd_m = await matches.update_many(
            {"players.deck_name": {"$regex": f"^{old_deck_name}$", "$options": "i"}},
            {"$set": {"players.$[elem].deck_name": new_deck_name}},
            array_filters=[{"elem.deck_name": {"$regex": f"^{old_deck_name}$", "$options": "i"}}],
        )

        await ctx.followup.send(embed=discord.Embed(
            title="Database Deck Name Updated",
            description=(f"Renamed '{old_deck_name}' → '{new_deck_name}'.\n"
                         f"Deck doc updated: **{upd_decks.modified_count}**, "
                         f"IR updated: **{upd_ir.modified_count}**, "
                         f"Matches updated: **{upd_m.modified_count}**."),
            color=0xFF0000 if IS_DEV else 0x00FF00), ephemeral=eph)
        
    from discord.commands import slash_command, Option


    @slash_command(
        guild_ids=[GUILD_ID],
        name="setplayer",
        description="Change the player in a seat (1-4)."
    )
    async def setplayer(
        self,
        ctx: discord.ApplicationContext,
        match_id: str = Option(str, "Match ID"),
        player: int = Option(int, "Player slot (1-4)"),
        new_player: discord.Member = Option(discord.Member, "New player"),
    ):
        eph = should_be_ephemeral(ctx)
        if not is_mod(ctx.author):
            await ctx.respond("You don’t have permission to use this.", ephemeral=True)
            return

        await ctx.defer(ephemeral=eph)

        # Locate match (accept str or int)
        m = await matches.find_one({"match_id": match_id})
        if not m:
            try:
                m = await matches.find_one({"match_id": int(match_id)})
            except Exception:
                m = None
        if not m:
            await ctx.followup.send("Match not found.", ephemeral=eph)
            return

        if not (1 <= player <= len(m.get("players", [])[:4])):
            await ctx.followup.send("Invalid player slot (must be 1–4).", ephemeral=eph)
            return

        idx = player - 1
        old_pid = m["players"][idx].get("player_id")
        new_pid = int(new_player.id)

        # No-op safety
        if str(old_pid) == str(new_pid):
            await ctx.followup.send("That seat already has this player.", ephemeral=eph)
            return

        # 1) Update matches at fixed index (no array filter needed)
        await matches.update_one(
            {"match_id": m["match_id"]},
            {"$set": {f"players.{idx}.player_id": new_pid}}
        )

        # 2) Update individual_results for this match/seat: move old_pid -> new_pid
        #    (defensively remove any existing IR rows for new_pid in this match to avoid dup)
        await individual_results.delete_many(
            {"match_id": m["match_id"], "player_id": {"$in": [str(new_pid), new_pid]}}
        )
        await individual_results.update_many(
            {"match_id": m["match_id"], "player_id": {"$in": [str(old_pid), old_pid]}},
            {"$set": {"player_id": new_pid}}
        )

        # 3) Recompute decks for all decks in this match (before+after), since attribution changed
        m2 = await matches.find_one({"match_id": m["match_id"]})

        def _deck_names_from_match(match_doc) -> set[str]:
            names = set()
            for p in (match_doc or {}).get("players", []):
                dn = p.get("deck_name")
                if isinstance(dn, str) and dn.strip():
                    names.add(dn.strip())
            return names

        affected_decks = _deck_names_from_match(m) | _deck_names_from_match(m2)
        if affected_decks:
            await recompute_deck_players_for(decks, individual_results, list(affected_decks))

        # 4) Re-open the editor so the mod can continue
        # Build the same summary you use in /edittrack
        lines = []
        def _mention(pid): return f"<@{pid}>"
        for i, p in enumerate(m2.get("players", [])[:4], start=1):
            raw_pos = p.get("position")
            try:
                pos_val = int(raw_pos)
            except (TypeError, ValueError):
                pos_val = None
            seat_txt = pos_val if pos_val in {1,2,3,4} else "?"
            lines.append(
                f"**P{i}** • {_mention(p.get('player_id','?'))} • "
                f"Seat {seat_txt} • Deck: *{p.get('deck_name','?')}* • "
                f"Result: **{p.get('result','?')}**"
            )

        embed = discord.Embed(
            title=f"Editing Match {m2.get('match_id')}",
            description="\n".join(lines) or "_No players?_",
            color=0xFF0000 if IS_DEV else 0x00FF00,
        )
        view = EditTrackView(author_id=ctx.author.id, match_doc=m2)

        await ctx.followup.send(
            f"✅ P{player} changed: `<@{old_pid}>` → <@{new_pid}>.\n"
            f"Reopened the editor below ⤵️",
            embed=embed,
            view=view,
            ephemeral=eph,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    
        
    @slash_command(
        guild_ids=[GUILD_ID],
        name="setplayerdeck",
        description="Set a player's deck with autocomplete."
    )
    async def setplayerdeck(
        self,
        ctx: discord.ApplicationContext,
        match_id: Annotated[str, Option(str, "Match ID")],
        player: Annotated[int, Option(int, "Player slot (1-4)")],
        deck: Annotated[str, Option(str, "Deck", autocomplete=deck_autocomplete)],
    ):
        eph = should_be_ephemeral(ctx)
        if not is_mod(ctx.author):
            await ctx.respond("You don’t have permission to use this.", ephemeral=True)
            return

        await ctx.defer(ephemeral=eph)

        # Locate match (accept str or int)
        m = await matches.find_one({"match_id": match_id})
        if not m:
            try:
                m = await matches.find_one({"match_id": int(match_id)})
            except Exception:
                m = None
        if not m:
            await ctx.followup.send("Match not found.", ephemeral=eph)
            return

        if not (1 <= player <= len(m.get("players", [])[:4])):
            await ctx.followup.send("Invalid player slot (must be 1–4).", ephemeral=eph)
            return

        idx = player - 1
        old = m["players"][idx].get("deck_name")

        # Update matches + IR for this player (handle str/int ids)
        pid = m["players"][idx]["player_id"]
        pid_vals = {str(pid)}
        try:
            pid_vals.add(int(str(pid)))
        except Exception:
            pass

        await matches.update_one(
            {"match_id": m["match_id"]},
            {"$set": {f"players.$[e].deck_name": deck}},
            array_filters=[{"e.player_id": {"$in": list(pid_vals)}}],
        )
        await individual_results.update_many(
            {"match_id": m["match_id"], "player_id": {"$in": list(pid_vals)}},
            {"$set": {"deck_name": deck}},
        )

        # Recompute decks for old+new deck names
        touched = [d for d in [old, deck] if isinstance(d, str) and d.strip()]
        if touched:
            await recompute_deck_players_for(decks, individual_results, touched)

        # Re-fetch match and re-open the editor with a fresh view
        m2 = await matches.find_one({"match_id": m["match_id"]})

        # Build the same summary lines you use in /edittrack
        lines = []
        def _mention(pid): return f"<@{pid}>"
        for i, p in enumerate(m2.get("players", [])[:4], start=1):
            raw_pos = p.get("position")
            try:
                pos_val = int(raw_pos)
            except (TypeError, ValueError):
                pos_val = None
            seat_txt = pos_val if pos_val in {1, 2, 3, 4} else "?"
            lines.append(
                f"**P{i}** • {_mention(p.get('player_id','?'))} • "
                f"Seat {seat_txt} • Deck: *{p.get('deck_name','?')}* • "
                f"Result: **{p.get('result','?')}**"
            )

        embed = discord.Embed(
            title=f"Editing Match {m2.get('match_id')}",
            description="\n".join(lines) or "_No players?_",
            color=0xFF0000 if IS_DEV else 0x00FF00,
        )

        # New editor view bound to this user
        view = EditTrackView(author_id=ctx.author.id, match_doc=m2)

        # Send the updated editor so the user can continue
        await ctx.followup.send(
            f"✅ P{player} deck set: `{old or '?'}` → **{deck}**.\n"
            f"Reopened the editor below ⤵️",
            embed=embed,
            view=view,
            ephemeral=eph,
            allowed_mentions=discord.AllowedMentions.none(),
        )


    # /edittrack
    @slash_command(
        guild_ids=[GUILD_ID],
        name="edittrack",
        description="Edit a tracked match (deck/result/seat). Mods only.",
    )
    async def edittrack(
        self,
        ctx: discord.ApplicationContext,
        match_id: Annotated[str, Option(str, "Match ID")],
    ):
        eph = should_be_ephemeral(ctx)
        if not is_mod(ctx.author):
            await ctx.respond(embed=discord.Embed(
                title="Permission Denied",
                description="You do not have permission to use this command.",
                color=0xFF0000), ephemeral=True)
            return

        # Allow match_id as str or int
        m = await matches.find_one({"match_id": match_id})
        if not m:
            try:
                m = await matches.find_one({"match_id": int(match_id)})
            except Exception:
                m = None
        if not m:
            await ctx.respond(f"No match found with id `{match_id}`.", ephemeral=eph)
            return

        # Show current state
        lines = []
        def _mention(pid): return f"<@{pid}>"

        for i, p in enumerate(m.get("players", [])[:4], start=1):
            raw_pos = p.get("position")  # matches uses position
            try:
                pos_val = int(raw_pos)
            except (TypeError, ValueError):
                pos_val = None
            seat_txt = pos_val if pos_val in {1, 2, 3, 4} else "?"
            lines.append(
                f"**P{i}** • {_mention(p.get('player_id','?'))} • "
                f"Seat {seat_txt} • Deck: *{p.get('deck_name','?')}* • "
                f"Result: **{p.get('result','?')}**"
            )

        embed = discord.Embed(
            title=f"Editing Match {m.get('match_id')}",
            description="\n".join(lines) or "_No players?_",
            color=0xFF0000 if IS_DEV else 0x00FF00,
        )
        view = EditTrackView(author_id=ctx.author.id, match_doc=m)
        await ctx.respond(embed=embed, view=view, ephemeral=eph, allowed_mentions=discord.AllowedMentions.none())

        # Wait until Apply/Cancel
        await view.wait()

        # If cancelled or no edits, do nothing
        if not view.edits:
            return
        
        # --- Validate final results (one-winner-or-all-draw) BEFORE writing ---
        final_results = _collect_proposed_results(m, view.edits)
        if not _results_valid(final_results):
            await ctx.followup.send(
                "❌ Invalid results: must be **one winner, rest losses**, or **all draw**. "
                f"Proposed results: {final_results}",
                ephemeral=True
            )
            return
        

        # Apply updates
        touched_decks: set[str] = set()

        for idx, changes in view.edits.items():
            if not changes:
                continue

            player = m["players"][idx]
            pid_raw = player.get("player_id")

            # match str/int player_id
            pid_str = str(pid_raw)
            pid_vals = {pid_str}
            try:
                pid_vals.add(int(pid_str))
            except Exception:
                pass

            match_set: Dict[str, object] = {}
            ir_set: Dict[str, object] = {}


            # result
            if "result" in changes:
                match_set["players.$[elem].result"] = changes["result"]
                ir_set["result"] = changes["result"]
                # result affects deck stats even if deck stays the same
                if isinstance(player.get("deck_name"), str) and player["deck_name"].strip():
                    touched_decks.add(player["deck_name"])

            # position / seat (doesn't affect stats, but safe to include)
            if "position" in changes:
                pos = int(changes["position"])
                match_set["players.$[elem].position"] = pos
                ir_set["seat"] = pos
                # optional: include current deck for completeness
                if isinstance(player.get("deck_name"), str) and player["deck_name"].strip():
                    touched_decks.add(player["deck_name"])

            # deck_name
            if "deck_name" in changes:
                new_deck = changes["deck_name"]
                old_deck = player.get("deck_name")
                match_set["players.$[elem].deck_name"] = new_deck
                ir_set["deck_name"] = new_deck
                if isinstance(old_deck, str) and old_deck.strip():
                    touched_decks.add(old_deck)
                if isinstance(new_deck, str) and new_deck.strip():
                    touched_decks.add(new_deck)

            if match_set:
                m_res = await matches.update_one(
                    {"match_id": m["match_id"]},
                    {"$set": match_set},
                    array_filters=[{"elem.player_id": {"$in": list(pid_vals)}}],
                )

            if ir_set:
                ir_res = await individual_results.update_many(
                    {"match_id": m["match_id"], "player_id": {"$in": list(pid_vals)}},
                    {"$set": ir_set},
                )

        # Re-fetch match AFTER updates
        m2 = await matches.find_one({"match_id": m["match_id"]})

        # Gather all deck names appearing in this match before and after edits
        def _deck_names_from_match(match_doc) -> set[str]:
            names = set()
            for p in (match_doc or {}).get("players", []):
                dn = p.get("deck_name")
                if isinstance(dn, str):
                    dn = dn.strip()
                    if dn:
                        names.add(dn)
            return names

        affected_decks = _deck_names_from_match(m) | _deck_names_from_match(m2)

        # Rebuild deck.stats from IR for all decks involved in this match
        if affected_decks:
            await recompute_deck_players_for(decks, individual_results, list(affected_decks))

        lines = []
        def _mention(pid): return f"<@{pid}>"

        for i, p in enumerate(m2.get("players", [])[:4], start=1):
            raw_pos = p.get("position")
            try:
                pos_val = int(raw_pos)
            except (TypeError, ValueError):
                pos_val = None
            seat_txt = pos_val if pos_val in {1, 2, 3, 4} else "?"
            lines.append(
                f"**P{i}** • {_mention(p.get('player_id','?'))} • "
                f"Seat {seat_txt} • Deck: *{p.get('deck_name','?')}* • "
                f"Result: **{p.get('result','?')}**"
            )

        updated = discord.Embed(
            title=f"Match {m2.get('match_id')} Updated",
            description="\n".join(lines) or "_No players?_",
            color=0xFF0000 if IS_DEV else 0x00FF00,
        )

        await ctx.followup.send(
            embed=updated,
            ephemeral=eph,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        
    # --- /deletetrack 


    @slash_command(
        guild_ids=[GUILD_ID],
        name="deletetrack",
        description="Delete a tracked match by ID (mods only) with confirm/cancel.",
    )
    async def deletetrack(
        self,
        ctx: discord.ApplicationContext,
        match: Annotated[str, Option(str, "Match ID or link")],
    ):
        eph = should_be_ephemeral(ctx)
        if not is_mod(ctx.author):
            await ctx.respond(
                embed=discord.Embed(title="Permission Denied", description="You do not have permission to use this command.", color=0xFF0000),
                ephemeral=True,
            )
            return

        mid = _parse_match_id(match)
        if not mid:
            await ctx.respond("Couldn't parse a match id from that input.", ephemeral=True)
            return

        m = await matches.find_one({"match_id": mid})
        if not m:
            await ctx.respond(f"No match found with id `{mid}`.", ephemeral=eph)
            return

        # show confirmation with buttons (only invoker can click)
        lines = []
        def _mention(pid): return f"<@{pid}>"
        for i, p in enumerate(m.get("players", [])[:4], start=1):
            try:
                seat = int(p.get("position"))
            except (TypeError, ValueError):
                seat = "?"
            lines.append(
                f"**P{i}** • {_mention(p.get('player_id','?'))} • "
                f"Seat {seat if seat in {1,2,3,4} else '?'} • "
                f"Deck: *{p.get('deck_name','?')}* • Result: **{p.get('result','?')}**"
            )

        embed = discord.Embed(
            title=f"Confirm deletion of match {m.get('match_id')}",
            description="\n".join(lines) or "_No players?_",
            color=0xFF7F7F,
        )
        view = DeleteTrackView(author_id=ctx.author.id, match_doc=m)
        await ctx.respond(embed=embed, view=view, ephemeral=eph, allowed_mentions=discord.AllowedMentions.none())

        
    @slash_command(guild_ids=[GUILD_ID], name="reindex", description="Ensure MongoDB indexes (mods only).")
    async def reindex(self, ctx: discord.ApplicationContext):
        if not is_mod(ctx.author):
            return await ctx.respond("Nope.", ephemeral=True)
        await ctx.defer(ephemeral=True)
        from db import ensure_indexes
        await ensure_indexes()
        await ctx.followup.send("Indexes ensured ✅", ephemeral=True)





def setup(bot):
    bot.add_cog(Admin(bot))
