import json
import unittest
from datetime import date, timedelta
from pathlib import Path

from collector import plan, run, score

TODAY = date(2026, 9, 18)


def obs(origin, dest, price, day=TODAY, code="DL", miles=760):
    return {"date": day.isoformat(), "origin": origin, "dest": dest, "name": dest, "country": "United States",
            "miles": miles, "price": price, "start": "2026-10-09", "end": "2026-10-12", "duration": 1,
            "airline": "Delta", "airline_code": code, "stops": 0}


def ver(origin, dest, price, rng, level="low", code="DL"):
    return {**obs(origin, dest, price, code=code), "verified_at": "2026-09-18T10:00:00+00:00",
            "airport": "EWR" if origin == "EWR" else "LGA",
            "insights": {"lowest_price": price, "price_level": level, "typical_price_range": rng}, "url": "https://x"}


class RobustZ(unittest.TestCase):
    def test_thin_history_gives_none(self):
        self.assertIsNone(score.robust_z(100, [200] * 5))

    def test_flat_route_uses_spread_floor(self):
        self.assertAlmostEqual(score.robust_z(190, [200] * 20), -1.0)

    def test_big_drop_is_very_negative(self):
        history = [300, 310, 290, 305, 295] * 4
        self.assertLess(score.robust_z(180, history), score.Z_RARE)


class Tier(unittest.TestCase):
    def test_tiers_from_typical_range(self):
        ins = {"price_level": "low", "typical_price_range": [200, 320]}
        self.assertEqual(score.tier(200, ins), "good")
        self.assertEqual(score.tier(160, ins), "great")
        self.assertEqual(score.tier(130, ins), "rare")
        self.assertIsNone(score.tier(240, {**ins, "price_level": "typical"}))

    def test_own_history_works_without_insights(self):
        self.assertEqual(score.tier(180, None, z=-2.3), "great")
        self.assertIsNone(score.tier(180, None, z=-1.0))
        self.assertIsNone(score.tier(180, {"typical_price_range": None}))

    def test_ulcc_penalty(self):
        self.assertEqual(score.effective_price(100, "NK"), 160)
        self.assertEqual(score.effective_price(100, "DL"), 100)


class EwrGate(unittest.TestCase):
    def test_needs_great_tier(self):
        self.assertFalse(score.ewr_passes(100, "good", 300))

    def test_needs_both_margins(self):
        self.assertFalse(score.ewr_passes(170, "great", 200))   # 15% but only $30
        self.assertFalse(score.ewr_passes(550, "great", 600))   # $50 but only 8%
        self.assertTrue(score.ewr_passes(160, "great", 200))

    def test_passes_when_nyc_has_no_service(self):
        self.assertTrue(score.ewr_passes(160, "rare", None))


class Candidates(unittest.TestCase):
    def test_cold_start_ranks_by_distance_curve_and_caps_ewr(self):
        # Fares on a clean curve, price = 2 * miles^0.6, except LIS which is far under it.
        routes = {"ATL": 760, "ORD": 740, "MIA": 1090, "DEN": 1630, "LAX": 2450, "SJU": 1600,
                  "LHR": 3450, "CDG": 3630, "FCO": 4280, "NRT": 6740, "BOS": 190}
        todays = [obs("NYC", d, round(2 * m ** 0.6), miles=m) for d, m in routes.items()]
        todays.append(obs("NYC", "LIS", 250, miles=3370))
        todays += [obs("EWR", "CLT", 40, miles=530), obs("EWR", "RDU", 35, miles=430)]
        picked = run.pick_candidates(todays, todays, [], 5, TODAY)
        self.assertIn("LIS", [o["dest"] for o in picked[:3]])
        self.assertEqual(sum(o["origin"] == "EWR" for o in picked), plan.VERIFY_EWR_MAX)

    def test_ewr_skipped_when_nyc_is_close(self):
        todays = [obs("NYC", "ATL", 100), obs("EWR", "ATL", 95)]
        picked = run.pick_candidates(todays, todays, [], 5, TODAY)
        self.assertEqual([o["origin"] for o in picked], ["NYC"])

    def test_recently_verified_skipped_unless_dropped(self):
        todays = [obs("NYC", "ATL", 100)]
        self.assertEqual(run.pick_candidates(todays, todays, [ver("NYC", "ATL", 102, [200, 300])], 5, TODAY), [])
        self.assertEqual(len(run.pick_candidates(todays, todays, [ver("NYC", "ATL", 130, [200, 300])], 5, TODAY)), 1)


class Deals(unittest.TestCase):
    def test_build_filters_and_sorts(self):
        verified = [
            ver("NYC", "ATL", 150, [200, 300]),                 # great
            ver("NYC", "ORD", 260, [200, 300], level="typical"),  # not a deal
            ver("NYC", "MIA", 120, [200, 300]),                 # rare
            ver("EWR", "ATL", 140, [200, 300]),                 # great but barely beats NYC -> hidden
            ver("EWR", "DEN", 120, [200, 300]),                 # rare, NYC has nothing -> shown
            ver("NYC", "FLL", 150, [200, 300], code="NK"),      # $210 effective -> not a deal
        ]
        deals = run.build_deals([], verified, TODAY)
        self.assertEqual([(d["dest"], d["is_ewr"]) for d in deals], [("MIA", False), ("DEN", True), ("ATL", False)])

    def test_stale_and_departed_deals_expire(self):
        old = {**ver("NYC", "ATL", 120, [200, 300]), "date": (TODAY - timedelta(days=4)).isoformat()}
        gone = {**ver("NYC", "MIA", 120, [200, 300]), "start": "2026-09-10"}
        self.assertEqual(run.build_deals([], [old, gone], TODAY), [])


class Fixtures(unittest.TestCase):
    """Real SerpApi responses captured 2026-09-18 (trimmed)."""

    def load(self, name):
        with open(Path(__file__).parent / "fixtures" / name) as fh:
            return json.load(fh)

    def test_parse_explore(self):
        q = {"origin": "NYC", "travel_duration": 1}
        rows = run.parse_explore(self.load("explore.json"), q, TODAY)
        self.assertGreater(len(rows), 40)
        lax = next(r for r in rows if r["dest"] == "LAX")
        self.assertEqual((lax["price"], lax["start"], lax["end"]), (281, "2026-10-31", "2026-11-02"))
        self.assertAlmostEqual(lax["miles"], 2450, delta=40)

    def test_parse_flights_skips_detours(self):
        o = {**obs("NYC", "ATL", 128), "thumbnail": None}
        v = run.parse_flights(self.load("flights.json"), o, TODAY)
        # $128 exists but routes LGA-FLL-ATL at ~2.5x the nonstop time.
        self.assertGreater(v["price"], 128)
        self.assertEqual(v["stops"], 0)
        self.assertIn(v["airport"], ("JFK", "LGA"))
        self.assertEqual(v["insights"]["typical_price_range"], [95, 255])
        self.assertTrue(v["url"].startswith("https://www.google.com/travel/flights"))


class Budget(unittest.TestCase):
    def test_daily_plan_fits_free_tier(self):
        q = plan.discovery_queries(TODAY)
        self.assertEqual([x["origin"] for x in q], ["NYC", "NYC", "EWR"])
        self.assertIsNone(q[0]["arrival_area_id"])
        self.assertLessEqual((len(q) + plan.VERIFY_PER_DAY) * 31, 250)

    def test_rotation_covers_every_region_and_trip_length(self):
        days = [plan.discovery_queries(TODAY + timedelta(days=d)) for d in range(6)]
        self.assertEqual({q[0]["travel_duration"] for q in days}, {1, 2})
        self.assertEqual({q[1]["area"] for q in days}, set(plan.REGIONS))
        self.assertEqual({q[2]["area"] for q in days}, set(plan.REGIONS) | {None})

    def test_low_balance_trims_plan(self):
        q = plan.discovery_queries(TODAY)
        self.assertEqual(plan.fit_budget(q, 250), (q, 5))
        self.assertEqual(plan.fit_budget(q, 6), (q, 1))
        self.assertEqual(plan.fit_budget(q, 3), (q[:1], 0))
        self.assertEqual(plan.fit_budget(q, 0), ([], 0))


if __name__ == "__main__":
    unittest.main()
