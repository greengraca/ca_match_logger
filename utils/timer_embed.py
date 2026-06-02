# utils/timer_embed.py
"""Pure helpers for the match-timer embed + progress bar. No config/env imports."""

import discord

PHASE_COLORS = {
    "running": 0x3498DB,  # blue
    "extra":   0xE67E22,  # orange
    "draw":    0xE74C3C,  # red
    "paused":  0x95A5A6,  # gray
}


def build_progress_bar(
    main_total: float,
    extra_total: float,
    remaining_main: float,
    remaining_total: float,
    *,
    width: int = 30,
) -> str:
    """Text bar: [██████░░░░|██░░░░] — left = main time, right = extra time."""
    main_total = max(float(main_total), 0.0)
    extra_total = max(float(extra_total), 0.0)
    total = main_total + extra_total
    if total <= 0:
        return "[----------]"

    width = max(width, 10)
    main_slots = max(1, int(round(width * (main_total / total))))
    extra_slots = max(1, width - main_slots)

    elapsed_total = main_total + extra_total - remaining_total
    elapsed_total = max(0.0, min(elapsed_total, main_total + extra_total))
    elapsed_main = main_total - remaining_main
    elapsed_main = max(0.0, min(elapsed_main, main_total))
    elapsed_extra = max(0.0, elapsed_total - elapsed_main)
    elapsed_extra = max(0.0, min(elapsed_extra, extra_total))

    main_fill = int(round(main_slots * (elapsed_main / main_total))) if main_total > 0 else main_slots
    main_fill = max(0, min(main_fill, main_slots))
    extra_fill = int(round(extra_slots * (elapsed_extra / extra_total))) if extra_total > 0 else 0
    extra_fill = max(0, min(extra_fill, extra_slots))

    filled_main = "█" * main_fill + "░" * (main_slots - main_fill)
    filled_extra = "█" * extra_fill + "░" * (extra_slots - extra_fill)
    return f"[{filled_main}|{filled_extra}]"


def pick_phase(remaining_main: float, remaining_total: float, phase_override=None) -> str:
    """running | extra | draw. phase_override='draw' forces the draw phase."""
    if phase_override == "draw":
        return "draw"
    if remaining_main > 0:
        return "running"
    if remaining_total > 0:
        return "extra"
    return "draw"


def build_timer_embed(
    vc_name: str,
    phase: str,                # "running" | "extra" | "draw" | "paused"
    main_total: float,
    extra_total: float,
    remaining_main: float,
    remaining_total: float,
    end_ts_main: int,
    end_ts_final: int,
    *,
    win_and_in: bool = False,
    title_prefix: str = "",
) -> discord.Embed:
    """Phase-colored timer embed with progress bar. Pure, no side effects."""
    color = PHASE_COLORS.get(phase, PHASE_COLORS["running"])
    titles = {
        "running": f"⏱️ {vc_name} — Timer Running",
        "extra":   f"⏱️ {vc_name} — Extra Time!",
        "draw":    f"⏱️ {vc_name} — Game Over",
        "paused":  f"⏸️ {vc_name} — Paused",
    }
    embed = discord.Embed(title=f"{title_prefix}{titles.get(phase, '⏱️ ' + vc_name)}", color=color)
    bar = build_progress_bar(main_total, extra_total, remaining_main, remaining_total)
    win_line = "🏆 **WIN & IN** — you must win to make the cut!\n" if win_and_in else ""

    if phase == "running":
        m, s = int(remaining_main // 60), int(remaining_main % 60)
        embed.add_field(name="Main Time", value=f"**{m}:{s:02d}** remaining", inline=False)
        embed.description = (
            f"{win_line}```{bar}```"
            f"\nMain time ends <t:{end_ts_main}:R> · Draw <t:{end_ts_final}:R>"
        )
        embed.set_footer(text="/pausetimer to pause · /endtimer to stop")
    elif phase == "extra":
        m, s = int(remaining_total // 60), int(remaining_total % 60)
        extra_minutes = int(extra_total / 60)
        embed.add_field(name="Extra Time", value=f"**{m}:{s:02d}** remaining", inline=False)
        embed.description = (
            f"{win_line}Time is over. You have **{extra_minutes} minutes** to finish "
            f"the active player's turn. Good luck!\n```{bar}```"
            f"\nDraw <t:{end_ts_final}:R>"
        )
        embed.set_footer(text="/pausetimer to pause · /endtimer to stop")
    elif phase == "draw":
        embed.description = f"```{bar}```\nIf no one won until now, the game is a draw. Well Played."
    elif phase == "paused":
        if remaining_main > 0:
            m, s = int(remaining_main // 60), int(remaining_main % 60)
            embed.add_field(name="Main Time", value=f"**{m}:{s:02d}** remaining", inline=False)
        else:
            m, s = int(remaining_total // 60), int(remaining_total % 60)
            embed.add_field(name="Extra Time", value=f"**{m}:{s:02d}** remaining", inline=False)
        embed.description = f"```{bar}```\nUse `/resumetimer` to continue."

    return embed
