"""Deal heuristic: route-relative scoring, tiers, and the EWR gate. Pure functions, no I/O."""

import math
from statistics import median

NYC = (40.7128, -74.0060)
MIN_HISTORY = 14          # observations of a route before we trust our own z-score
ULCC = {"NK", "F9", "G4", "XP", "MX", "SY"}  # Spirit, Frontier, Allegiant, Avelo, Breeze, Sun Country
ULCC_PENALTY = 60         # rough round-trip carry-on cost, added before comparing fares

# Going's published grades: average sent deal ~2.2 sd under the route mean, "top 15%" at 2.6.
Z_GREAT, Z_RARE = -2.2, -2.6
PCT_GREAT, PCT_RARE = 0.20, 0.35

EWR_MIN_PCT, EWR_MIN_USD = 0.15, 40

TIER_RANK = {"rare": 0, "great": 1, "good": 2}


def miles_from_nyc(lat, lon):
    lat1, lon1, lat2, lon2 = map(math.radians, (*NYC, lat, lon))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def effective_price(price, airline_code):
    return price + (ULCC_PENALTY if airline_code in ULCC else 0)


def robust_z(price, history):
    """Median/MAD z-score of price against past prices for the same route. None if history is too thin."""
    if len(history) < MIN_HISTORY:
        return None
    med = median(history)
    mad = median(abs(p - med) for p in history)
    # Flat routes have MAD 0; floor the spread at 5% of the median so a $1 dip isn't "rare".
    spread = max(1.4826 * mad, 0.05 * med)
    return (price - med) / spread


def fit_distance_curve(observations):
    """Least-squares fit of log(price) on log(miles) across observations. Returns (a, b, sd) or None.

    Fares grow sub-linearly with distance, so plain cents-per-mile always favors the longest
    routes in any bucket. Ranking by residual from this curve doesn't.
    """
    pts = [(math.log(max(o["miles"], 100)), math.log(effective_price(o["price"], o.get("airline_code"))))
           for o in observations]
    if len(pts) < 10:
        return None
    mx, my = sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in pts) / sxx
    a = my - b * mx
    sd = math.sqrt(sum((y - a - b * x) ** 2 for x, y in pts) / len(pts))
    return a, b, max(sd, 0.05)


def candidate_score(obs, history, curve):
    """Lower is better. Own z-score when we have history, else how far under the distance curve the fare is."""
    price = effective_price(obs["price"], obs.get("airline_code"))
    z = robust_z(price, history)
    if z is not None:
        return z
    if not curve:
        return 0.0
    a, b, sd = curve
    return (math.log(price) - a - b * math.log(max(obs["miles"], 100))) / sd


def tier(price, insights, z=None):
    """Return 'rare' | 'great' | 'good' | None for a verified price.

    insights is Google's price_insights (may be None/partial); z is our own robust z (may be None).
    """
    low = None
    rng = (insights or {}).get("typical_price_range")
    if rng and len(rng) == 2 and rng[0] > 0:
        low = rng[0]
    under = (low - price) / low if low else None

    if (under is not None and under >= PCT_RARE) or (z is not None and z <= Z_RARE):
        return "rare"
    if (under is not None and under >= PCT_GREAT) or (z is not None and z <= Z_GREAT):
        return "great"
    if low and (insights or {}).get("price_level") == "low" and price <= low:
        return "good"
    return None


def ewr_passes(ewr_price, deal_tier, best_jfk_lga_price):
    """EWR only shows when it's a great deal and clearly beats JFK/LGA to the same place."""
    if deal_tier not in ("great", "rare"):
        return False
    if best_jfk_lga_price is None:
        return True  # JFK/LGA don't serve it at all (within our stop limit)
    saving = best_jfk_lga_price - ewr_price
    return saving >= EWR_MIN_USD and saving / best_jfk_lga_price >= EWR_MIN_PCT
