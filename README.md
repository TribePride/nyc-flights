# NYC Flight Deals

A static page of round-trip fares out of New York that are well under normal. JFK and LGA by default; Newark only when it is a great deal and clearly beats both.

A daily GitHub Actions job pulls Google Flights data through SerpApi (free tier, 250 searches a month), appends it to `data/`, rebuilds `site/deals.json`, and deploys `site/` to GitHub Pages. No server, no dependencies beyond Python 3.

## How it works

1. **Discover** — 3 `google_travel_explore` calls a day: JFK+LGA to North America (alternating weekend and week-long trips), JFK+LGA to one rotating region (Europe, Caribbean, South America, Asia), and one EWR call that walks through the same six. Each asks for the cheapest dates in the next 6 months. Every destination price is stored in `data/observations/`.
2. **Score** — each route is ranked against its own 90-day history (median/MAD z-score). Until a route has 14 days of history, it is ranked by how far its fare sits under a price-vs-distance curve fitted to that day's data.
3. **Verify** — the top 5 candidates get a `google_flights` call, which returns the live price plus Google's typical price range. Itineraries more than 1.75x the fastest option's travel time are ignored, so a $128 fare that connects through Florida to reach Atlanta doesn't count. Stored in `data/verified/`.
4. **Publish** — a verified fare is shown if it is Good (at or under typical low), Great (20%+ under, or z ≤ −2.2) or Rare (35%+ under, or z ≤ −2.6). Deals expire after 3 days.
5. **Honorable mentions** — on days with no deals at all, the page lists up to 6 near misses instead: checked fares in the bottom 35% of their typical range, JFK/LGA first.

Newark fares must be Great or better and beat the best JFK/LGA fare to the same place by at least 15% and $40. Spirit, Frontier and similar get $60 added before any comparison.

## Watched trips

`watchlist.json` lists fixed-date trips to check every morning, shown at the top of the page as "Your trips". Each costs one search a day (JFK, LGA and EWR in a single call) and comes out of the verification slots, so the daily total stays at 8: with 3 watches, that's 3 watches + 3 discovery + 2 verifications. A watch stops on its own once the departure date passes. The card shows the best sensible JFK/LGA fare (Newark only if it wins by 15% and $40), Google's low/typical/high call for those dates, the move since the last check, the lowest price seen, and any cheaper option the rules skipped (a long layover, or a Newark fare that didn't clear the margin).

```json
{"id": "chicago-oct", "name": "Chicago", "arrival_id": "ORD,MDW", "start": "2026-10-09", "end": "2026-10-12"}
```

All thresholds live at the top of `collector/score.py`; the query budget lives in `collector/plan.py`. The reasoning behind them is in `docs/research.md`.

## Run it

```sh
python3 -m unittest -q                 # tests, no network
python3 -m collector.run --dry-run     # show today's query plan, spends nothing
SERPAPI_KEY=... python3 -m collector.run   # real run, up to 8 searches
python3 -m collector.run --watches-only   # check just the watched trips (one search each)
python3 -m collector.run --build       # rebuild site/deals.json from stored data
python3 -m http.server -d site 8000    # view it; add ?sample to see example cards
```

## Deploy

1. Get a free key at serpapi.com.
2. Push this repo to GitHub, add the key as the `SERPAPI_KEY` Actions secret.
3. Settings → Pages → Source: **GitHub Actions**.
4. Run the `daily` workflow once by hand. It then runs every morning at 10:00 UTC.
