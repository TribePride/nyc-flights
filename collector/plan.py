"""Decide which queries to spend today's SerpApi budget on."""

from datetime import date

ORIGINS = {"NYC": "JFK,LGA", "EWR": "EWR"}

# Explore with no area only returns North America, so other regions need their own call.
# Values are Google Knowledge Graph ids.
REGIONS = {"Europe": "/m/02j9z", "Caribbean": "/m/0261m", "South America": "/m/06n3y", "Asia": "/m/0j0k"}

WEEKEND, WEEK = 1, 2
ANY_MONTH = 0             # cheapest dates in the next 6 months

VERIFY_PER_DAY = 5
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


def fit_budget(queries, searches_left):
    """Trim the plan to what the account can afford. Returns (discovery queries, verification slots)."""
    spendable = max(searches_left - RESERVE, 0)
    queries = queries[:spendable]
    return queries, min(VERIFY_PER_DAY, spendable - len(queries))
