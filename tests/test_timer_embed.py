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


from utils.timer_embed import build_timer_embed, PHASE_COLORS


def _running(**kw):
    base = dict(vc_name="Mesa 1", phase="running", main_total=4500, extra_total=900,
                remaining_main=3150, remaining_total=4050, end_ts_main=111, end_ts_final=222)
    base.update(kw)
    return build_timer_embed(**base)


def test_running_embed_basics():
    e = _running()
    assert "Mesa 1" in e.title and "Running" in e.title
    assert e.color.value == PHASE_COLORS["running"]
    assert any(f.name == "Main Time" for f in e.fields)
    assert "Players" not in [f.name for f in e.fields]   # TopDeck dropped
    assert "```[" in e.description                        # bar present


def test_extra_embed_mentions_15_minutes():
    e = build_timer_embed("Mesa 1", "extra", 4500, 900, 0, 600, 111, 222)
    assert "Extra Time" in e.title
    assert e.color.value == PHASE_COLORS["extra"]
    assert "15 minutes" in e.description                  # 900s extra_total


def test_draw_embed_has_no_main_time_field():
    e = build_timer_embed("Mesa 1", "draw", 4500, 900, 0, 0, 111, 222)
    assert "Game Over" in e.title
    assert e.color.value == PHASE_COLORS["draw"]
    assert e.fields == [] or all(f.name != "Main Time" for f in e.fields)
    assert "Well Played" in e.description


def test_paused_embed_color():
    e = build_timer_embed("Mesa 1", "paused", 4500, 900, 1200, 2100, 0, 0)
    assert "Paused" in e.title
    assert e.color.value == PHASE_COLORS["paused"]


def test_win_and_in_line():
    e = _running(win_and_in=True)
    blob = e.description + "".join(f.value for f in e.fields)
    assert "WIN & IN" in blob


def test_title_prefix():
    e = _running(title_prefix="(DEV) ")
    assert e.title.startswith("(DEV) ")
