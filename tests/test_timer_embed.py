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
