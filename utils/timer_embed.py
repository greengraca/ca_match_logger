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
