from utils.timer_embed import build_progress_bar


def test_bar_empty_at_start():
    # 75 min main (4500s), 15 min extra (900s), nothing elapsed
    bar = build_progress_bar(4500, 900, 4500, 5400)
    # width 30: main_slots=25, extra_slots=5, all empty
    assert bar == "[" + "░" * 25 + "|" + "░" * 5 + "]"


def test_bar_partway_through_main():
    # 60 min elapsed of main -> 15 min main remaining; extra untouched
    bar = build_progress_bar(4500, 900, 900, 1800)
    assert bar == "[" + "█" * 20 + "░" * 5 + "|" + "░" * 5 + "]"


def test_bar_full_at_draw():
    bar = build_progress_bar(4500, 900, 0, 0)
    assert bar == "[" + "█" * 25 + "|" + "█" * 5 + "]"


def test_bar_zero_total():
    assert build_progress_bar(0, 0, 0, 0) == "[----------]"


from utils.timer_embed import pick_phase


def test_phase_running():
    assert pick_phase(100, 200, None) == "running"


def test_phase_extra():
    assert pick_phase(0, 200, None) == "extra"


def test_phase_draw_when_nothing_left():
    assert pick_phase(0, 0, None) == "draw"


def test_phase_override_forces_draw():
    assert pick_phase(100, 200, "draw") == "draw"
