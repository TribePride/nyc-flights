"""Thin SerpApi client. Everything vendor-specific lives here so the source can be swapped."""

import json
import os
import urllib.parse
import urllib.request

BASE = "https://serpapi.com"


class SerpError(RuntimeError):
    pass


def _get(path, params):
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        raise SerpError("SERPAPI_KEY is not set")
    query = urllib.parse.urlencode({**params, "api_key": key})
    try:
        with urllib.request.urlopen(f"{BASE}{path}?{query}", timeout=90) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        raise SerpError(f"HTTP {e.code} from SerpApi: {e.read()[:300]!r}") from e
    if body.get("error"):
        raise SerpError(body["error"])
    return body


def searches_left():
    """Free endpoint, does not count against the quota."""
    return int(_get("/account", {}).get("total_searches_left", 0))


def explore(departure_id, travel_duration, month, arrival_area_id=None, stops=2):
    """Cheapest round-trip destinations from departure_id (comma-separated airports allowed).

    travel_duration: 1 weekend, 2 one week, 3 two weeks. month: 0 = any time in the next 6 months.
    stops: 0 any, 1 nonstop, 2 one or fewer. Without arrival_area_id Google only returns North America.
    """
    area = {"arrival_area_id": arrival_area_id} if arrival_area_id else {}
    return _get("/search", {
        **area,
        "engine": "google_travel_explore",
        "departure_id": departure_id,
        "travel_duration": travel_duration,
        "month": month,
        "stops": stops,
        "currency": "USD",
        "hl": "en",
        "gl": "us",
    })


def flights(departure_id, arrival_id, outbound_date, return_date, stops=2):
    """Round-trip search for fixed dates. Response carries price_insights when Google has it."""
    return _get("/search", {
        "engine": "google_flights",
        "departure_id": departure_id,
        "arrival_id": arrival_id,
        "outbound_date": outbound_date,
        "return_date": return_date,
        "type": 1,
        "stops": stops,
        "currency": "USD",
        "hl": "en",
        "gl": "us",
    })
