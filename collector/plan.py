"""Decide which queries to spend today's SerpApi budget on."""

import json
from datetime import date
from pathlib import Path

ORIGINS = {"NYC": "JFK,LGA", "EWR": "EWR"}

# Explore with no area only returns North America, so other regions need their own call.
# Values are Google Knowledge Graph ids.
REGIONS = {"Europe": "/m/02j9z", "Caribbean": "/m/0261m", "South America": "/m/06n3y", "Asia": "/m/0j0k"}

WEEKEND, WEEK = 1, 2
ANY_MONTH = 0             # cheapest dates in the next 6 months

DAILY_BUDGET = 8          # 8 x 31 = 248, just under the free tier's 250 a month
WATCHLIST = Path(__file__).resolve().parent.parent / "watchlist.json"
ALL_NYC = "JFK,LGA,EWR"   # a watch checks all three in one call; EWR still has to clearly win to be shown
VERIFY_EWR_MAX = 1        # EWR never takes more than one verification slot
RESERVE = 2               # never spend the last few searches


def _query(origin, duration, area=None):
    return {"origin": origin, "departure_id": ORIGINS[origin], "travel_duration": duration,
            "month": ANY_MONTH, "area": area, "arrival_area_id": REGIONS.get(area)}


def discovery_queries(today=None):
    """Three calls a day: NYC to North America, NYC to one rotating region, and one EWR call.

    North America alternates weekend/week-long daily; far regions are week-long only and come
    round every 4 days. EWR walks through all six of those in turn.
    """
    today = today or date.today()
    day = today.toordinal()
    regions = list(REGIONS)
    ewr_cycle = [(WEEKEND, None), (WEEK, None)] + [(WEEK, r) for r in regions]
    return [
        _query("NYC", (WEEKEND, WEEK)[day % 2]),
        _query("NYC", WEEK, regions[day % len(regions)]),
        _query("EWR", *ewr_cycle[day % len(ewr_cycle)]),
    ]


def active_watches(today=None):
    """Fixed-date trips from watchlist.json that haven't departed yet."""
    today = today or date.today()
    if not WATCHLIST.exists():
        return []
    return [w for w in json.loads(WATCHLIST.read_text()) if w["start"] > today.isoformat()]


def due_watches(today=None):
    """Active watches to check today. A watch with "every": N is only checked every Nth day, to save searches."""
    today = today or date.today()
    return [w for w in active_watches(today) if today.toordinal() % w.get("every", 1) == 0]


def fit_budget(watches, queries, searches_left):
    """Split today's searches. Watches come first, then discovery, and verification gets what's left.

    Returns (watches, discovery queries, verification slots).
    """
    spendable = min(max(searches_left - RESERVE, 0), DAILY_BUDGET)
    watches = watches[:spendable]
    queries = queries[:spendable - len(watches)]
    return watches, queries, spendable - len(watches) - len(queries)
