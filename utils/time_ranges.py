from datetime import datetime, timezone, timedelta

POSTBAN_START = datetime(2024, 9, 24, tzinfo=timezone.utc)

def format_period(period: str) -> str:
    return {
        "1m": "Last 30 Days",
        "3m": "Last 3 Months",
        "6m": "Last 6 Months",
        "1y": "Last Year",
        "all": "Eternal",
    }.get(period, "Custom Period")

def get_period_start(period: str, postban: bool) -> datetime:
    now = datetime.now(timezone.utc)
    if postban:
        base = {
            "1m": now - timedelta(days=30),
            "3m": now - timedelta(days=90),
            "6m": now - timedelta(days=180),
            "1y": now - timedelta(days=365),
            "all": POSTBAN_START,
        }.get(period, POSTBAN_START)
        return max(base, POSTBAN_START)
    else:
        return {
            "1m": now - timedelta(days=30),
            "3m": now - timedelta(days=90),
            "6m": now - timedelta(days=180),
            "1y": now - timedelta(days=365),
            "all": datetime.min.replace(tzinfo=timezone.utc),
        }.get(period, datetime.min.replace(tzinfo=timezone.utc))

def previous_month_window(period: str):
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    if period != "1m":
        return None, None
    cur_start = now - timedelta(days=30)
    prev_start = cur_start - timedelta(days=30)
    return prev_start, cur_start
