"""Daily run: discover -> score -> verify -> write data and site/deals.json.

    python -m collector.run            # real run, spends SerpApi searches
    python -m collector.run --dry-run  # print today's plan, spend nothing
    python -m collector.run --build    # rebuild deals.json from stored data only
"""

import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import plan, score, serp

ROOT = Path(__file__).resolve().parent.parent
OBS_DIR = ROOT / "data" / "observations"
VER_DIR = ROOT / "data" / "verified"
DEALS = ROOT / "site" / "deals.json"

HISTORY_DAYS = 90
DEAL_TTL_DAYS = 3
MENTION_MAX_POSITION = 0.35   # honorable mention: within the bottom 35% of the typical range
MENTION_LIMIT = 6
MAX_DETOUR = 1.75         # skip itineraries this many times longer than the fastest option
TRIP_DAYS = {1: 3, 2: 7, 3: 14}
DURATION_LABEL = {1: "weekend", 2: "week", 3: "two weeks"}


def _read(dirpath, since):
    rows = []
    for f in sorted(dirpath.glob("*.jsonl")):
        if f.stem < since.strftime("%Y-%m"):
            continue
        with f.open() as fh:
            rows += [r for r in map(json.loads, fh) if r["date"] >= since.isoformat()]
    return rows


def _append(dirpath, rows, today):
    if not rows:
        return
    dirpath.mkdir(parents=True, exist_ok=True)
    with (dirpath / f"{today:%Y-%m}.jsonl").open("a") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")


def parse_explore(body, query, today):
    rows = []
    for d in body.get("destinations", []):
        code = (d.get("destination_airport") or {}).get("code")
        gps = d.get("gps_coordinates") or {}
        if not (code and d.get("flight_price") and d.get("start_date") and "latitude" in gps):
            continue
        start = date.fromisoformat(d["start_date"])
        end = d.get("end_date") or (start + timedelta(days=TRIP_DAYS[query["travel_duration"]])).isoformat()
        rows.append({
            "date": today.isoformat(),
            "origin": query["origin"],
            "dest": code,
            "name": d.get("name"),
            "country": d.get("country"),
            "miles": round(score.miles_from_nyc(gps["latitude"], gps["longitude"])),
            "price": d["flight_price"],
            "start": d["start_date"],
            "end": end,
            "duration": query["travel_duration"],
            "airline": d.get("airline"),
            "airline_code": d.get("airline_code"),
            "stops": d.get("number_of_stops"),
            "thumbnail": d.get("thumbnail"),
        })
    return rows


def route_history(observations):
    """(origin, dest, duration) -> daily-minimum effective prices. Weekends and week-long trips price differently."""
    daily = defaultdict(dict)
    for o in observations:
        p = score.effective_price(o["price"], o.get("airline_code"))
        day = daily[(o["origin"], o["dest"], o["duration"])]
        day[o["date"]] = min(p, day.get(o["date"], p))
    return {k: list(v.values()) for k, v in daily.items()}


def best_nyc_price(observations, dest, since):
    prices = [score.effective_price(o["price"], o.get("airline_code")) for o in observations
              if o["origin"] == "NYC" and o["dest"] == dest and o["date"] >= since.isoformat()]
    return min(prices) if prices else None


def pick_candidates(todays, observations, verified, slots, today):
    """Rank today's observations and choose which to spend verification calls on."""
    history = route_history(observations)
    curve = score.fit_distance_curve(todays)

    recent = {}
    for v in verified:
        if v["date"] >= (today - timedelta(days=DEAL_TTL_DAYS)).isoformat():
            recent[(v["origin"], v["dest"])] = v["price"]

    ranked = []
    for o in todays:
        key = (o["origin"], o["dest"])
        if key in recent and o["price"] > 0.9 * recent[key]:
            continue  # checked recently and it hasn't dropped another 10%
        if o["origin"] == "EWR":
            nyc = best_nyc_price(observations, o["dest"], today - timedelta(days=7))
            eff = score.effective_price(o["price"], o.get("airline_code"))
            if not score.ewr_passes(eff, "great", nyc):
                continue
        s = score.candidate_score(o, history.get((*key, o["duration"]), []), curve)
        ranked.append((s, o))
    ranked.sort(key=lambda t: t[0])

    chosen, seen, ewr = [], set(), 0
    for _, o in ranked:
        key = (o["origin"], o["dest"])
        if key in seen:
            continue
        if o["origin"] == "EWR":
            if ewr >= plan.VERIFY_EWR_MAX:
                continue
            ewr += 1
        seen.add(key)
        chosen.append(o)
        if len(chosen) >= slots:
            break
    return chosen


def parse_flights(body, obs, today):
    options = (body.get("best_flights") or []) + (body.get("other_flights") or [])
    options = [o for o in options if o.get("price") and o.get("total_duration")]
    if not options:
        return None
    # The absolute cheapest is often a long detour; take the cheapest itinerary that isn't.
    fastest = min(o["total_duration"] for o in options)
    best = min((o for o in options if o["total_duration"] <= MAX_DETOUR * fastest), key=lambda o: o["price"])
    legs = best.get("flights") or [{}]
    codes = {(leg.get("flight_number") or "").split(" ")[0] for leg in legs}
    ulcc = sorted(codes & score.ULCC)
    return {
        "date": today.isoformat(),
        "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin": obs["origin"],
        "airport": (legs[0].get("departure_airport") or {}).get("id"),
        "dest": obs["dest"],
        "name": obs["name"],
        "country": obs["country"],
        "miles": obs["miles"],
        "price": best["price"],
        "start": obs["start"],
        "end": obs["end"],
        "duration": obs["duration"],
        "airline": legs[0].get("airline") or obs.get("airline"),
        "airline_code": ulcc[0] if ulcc else (legs[0].get("flight_number") or "").split(" ")[0],
        "stops": len(legs) - 1,
        "insights": {k: (body.get("price_insights") or {}).get(k)
                     for k in ("lowest_price", "price_level", "typical_price_range")},
        "url": (body.get("search_metadata") or {}).get("google_flights_url"),
        "thumbnail": obs.get("thumbnail"),
    }


def _cards(observations, verified, today):
    """One card per recently verified route, tier set to None when it misses the bar. Applies the EWR gate."""
    history = route_history(observations)
    cutoff = (today - timedelta(days=DEAL_TTL_DAYS)).isoformat()
    latest = {}
    for v in verified:
        if v["date"] >= cutoff and v["start"] > today.isoformat():
            latest[(v["origin"], v["dest"])] = v  # rows are in date order, last one wins

    cards = []
    for (origin, dest), v in latest.items():
        eff = score.effective_price(v["price"], v.get("airline_code"))
        z = score.robust_z(eff, history.get((origin, dest, v["duration"]), []))
        t = score.tier(eff, v.get("insights"), z)
        saving = None
        if origin == "EWR":
            nyc = latest.get(("NYC", dest))
            nyc_price = (score.effective_price(nyc["price"], nyc.get("airline_code")) if nyc
                         else best_nyc_price(observations, dest, today - timedelta(days=7)))
            if not score.ewr_passes(eff, "great", nyc_price):
                continue  # doesn't beat JFK/LGA by enough to show anywhere
            if t == "good":
                t = None  # EWR needs great+ to be a deal; a merely good one can still earn a mention
            saving = nyc_price - eff if nyc_price else None
        rng = (v.get("insights") or {}).get("typical_price_range")
        cards.append({
            "tier": t,
            "origin": v.get("airport") or ("EWR" if origin == "EWR" else "JFK/LGA"),
            "is_ewr": origin == "EWR",
            "ewr_saving": saving,
            "dest": dest,
            "name": v["name"],
            "country": v["country"],
            "domestic": v["country"] == "United States",
            "price": v["price"],
            "typical": rng,
            "under_pct": round(100 * (rng[0] - eff) / rng[0]) if rng and rng[0] else None,
            "position": round((eff - rng[0]) / (rng[1] - rng[0]), 2) if rng and rng[1] > rng[0] else None,
            "z": round(z, 1) if z is not None else None,
            "start": v["start"],
            "end": v["end"],
            "trip": DURATION_LABEL[v["duration"]],
            "airline": v["airline"],
            "stops": v["stops"],
            "bare_fare": v.get("airline_code") in score.ULCC,
            "url": v["url"],
            "thumbnail": v.get("thumbnail"),
            "verified_at": v["verified_at"],
        })
    return cards


def build_deals(observations, verified, today):
    deals = [c for c in _cards(observations, verified, today) if c["tier"]]
    deals.sort(key=lambda d: (score.TIER_RANK[d["tier"]], d["is_ewr"], -(d["under_pct"] or 0)))
    return deals


def build_mentions(observations, verified, today):
    """Near misses for days with no deals: checked fares sitting in the low end of Google's typical range."""
    near = [c for c in _cards(observations, verified, today)
            if not c["tier"] and c["position"] is not None and c["position"] <= MENTION_MAX_POSITION]
    near.sort(key=lambda c: (c["is_ewr"], c["position"]))
    return near[:MENTION_LIMIT]


def write_deals(observations, verified, today):
    DEALS.parent.mkdir(parents=True, exist_ok=True)
    deals = build_deals(observations, verified, today)
    DEALS.write_text(json.dumps({
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "routes_tracked": len({(o["origin"], o["dest"]) for o in observations}),
        "deals": deals,
        "honorable_mentions": [] if deals else build_mentions(observations, verified, today),
    }, indent=1))
    return deals


def main(argv):
    today = date.today()
    since = today - timedelta(days=HISTORY_DAYS)
    observations, verified = _read(OBS_DIR, since), _read(VER_DIR, since)

    if "--build" in argv:
        print(f"{len(write_deals(observations, verified, today))} deals written")
        return 0

    queries = plan.discovery_queries(today)
    if "--dry-run" in argv:
        for q in queries:
            print("explore", q)
        print(f"+ up to {plan.VERIFY_PER_DAY} verifications = {len(queries) + plan.VERIFY_PER_DAY} calls max")
        return 0

    left = serp.searches_left()
    queries, slots = plan.fit_budget(queries, left)
    print(f"{left} searches left; running {len(queries)} discovery, up to {slots} verification")

    todays = []
    for q in queries:
        try:
            rows = parse_explore(serp.explore(q["departure_id"], q["travel_duration"], q["month"], q["arrival_area_id"]), q, today)
        except serp.SerpError as e:
            print(f"explore failed {q}: {e}", file=sys.stderr)
            continue
        print(f"explore {q['origin']} -> {q['area'] or 'North America'} dur={q['travel_duration']}: {len(rows)} destinations")
        todays += rows
    _append(OBS_DIR, todays, today)
    observations += todays

    new = []
    for o in pick_candidates(todays, observations, verified, slots, today):
        try:
            v = parse_flights(serp.flights(plan.ORIGINS[o["origin"]], o["dest"], o["start"], o["end"]), o, today)
        except serp.SerpError as e:
            print(f"verify failed {o['origin']}-{o['dest']}: {e}", file=sys.stderr)
            continue
        if v:
            print(f"verified {v['airport']}-{v['dest']} ${v['price']} level={v['insights']['price_level']} "
                  f"typical={v['insights']['typical_price_range']}")
            new.append(v)
    _append(VER_DIR, new, today)
    verified += new

    print(f"{len(write_deals(observations, verified, today))} deals on the site")
    return 0 if todays or not queries else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
