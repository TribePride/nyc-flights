"""Find the cheapest flexible dates to a country or region, to pick dates for a watch.

    python -m collector.find /m/01p1v --month 12        # Costa Rica in December: cheapest weekend, week, two weeks
    python -m collector.find /m/01p1v --month 12 --durations 1,2

One SerpApi explore call per trip length, from JFK, LGA and EWR together. Prints the cheapest dates per
airport and spends nothing on verification; add the dates you like to watchlist.json and check them with
--watches-only --only <id>. Google's explore only takes a month and a trip length, not a date window.
"""

import sys
from datetime import date

from . import plan, serp
from .run import DURATION_LABEL, parse_explore


def find(area_id, month, durations, today=None):
    today = today or date.today()
    rows = []
    for duration in durations:
        query = {"origin": "NYC", "travel_duration": duration}
        rows += parse_explore(serp.explore(plan.ALL_NYC, duration, month, area_id), query, today)
    return sorted(rows, key=lambda r: r["price"])


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    area_id = argv[0]
    month = int(argv[argv.index("--month") + 1]) if "--month" in argv else plan.ANY_MONTH
    durations = [int(d) for d in argv[argv.index("--durations") + 1].split(",")] if "--durations" in argv else [1, 2, 3]
    for r in find(area_id, month, durations):
        print(f"${r['price']:<5} {r['dest']} {r['name']:<22} {r['start']} -> {r['end']}  {DURATION_LABEL[r['duration']]:<9} "
              f"{r['airline'] or '?'} stops={r['stops']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
