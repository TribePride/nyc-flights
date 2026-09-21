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


class Mentions(unittest.TestCase):
    def test_near_misses_ranked_and_capped_to_low_end(self):
        verified = [
            ver("NYC", "ATL", 230, [200, 300], level="typical"),   # 30% into the range -> mention
            ver("NYC", "ORD", 210, [200, 300], level="typical"),   # 10% -> mention, ranked first
            ver("NYC", "BOS", 260, [200, 300], level="typical"),   # 60% -> no
            ver("NYC", "MIA", 150, [200, 300]),                    # a real deal, never a mention
            ver("EWR", "DEN", 220, [200, 300], level="typical"),   # no NYC fare to compare -> mention, after NYC
            ver("EWR", "ATL", 215, [200, 300], level="typical"),   # doesn't beat NYC's $230 by enough -> no
            ver("EWR", "SFO", 190, [200, 300]),                    # only "good", so not an EWR deal, but a mention
        ]
        self.assertEqual([m["dest"] for m in run.build_mentions([], verified, TODAY)], ["ORD", "ATL", "SFO", "DEN"])
        self.assertEqual([d["dest"] for d in run.build_deals([], verified, TODAY)], ["MIA"])


class Movement(unittest.TestCase):
    def test_up_down_and_first_check(self):
        m = run.movement({"2026-09-16": 210, "2026-09-17": 180}, "2026-09-18", 195)
        self.assertEqual((m["change"], m["change_since"], m["change_total"]), (15, "2026-09-17", -15))
        self.assertEqual(m["history"][-1], ["2026-09-18", 195])
        m = run.movement({}, "2026-09-18", 195)
        self.assertEqual((m["change"], m["change_total"], len(m["history"])), (None, None, 1))

    def test_verified_price_replaces_same_day_listing(self):
        m = run.movement({"2026-09-17": 200, "2026-09-18": 190}, "2026-09-18", 230)
        self.assertEqual((m["change"], [p for _, p in m["history"]]), (30, [200, 230]))

    def test_deal_cards_carry_route_movement(self):
        past = [obs("NYC", "MIA", 170, day=TODAY - timedelta(days=2)), obs("NYC", "MIA", 150, day=TODAY - timedelta(days=1))]
        deal = run.build_deals(past, [ver("NYC", "MIA", 120, [200, 300])], TODAY)[0]
        self.assertEqual((deal["change"], deal["change_total"]), (-30, -50))


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
        self.assertLessEqual(plan.DAILY_BUDGET * 31, 250)

    def test_rotation_covers_every_region_and_trip_length(self):
        days = [plan.discovery_queries(TODAY + timedelta(days=d)) for d in range(6)]
        self.assertEqual({q[0]["travel_duration"] for q in days}, {1, 2})
        self.assertEqual({q[1]["area"] for q in days}, set(plan.REGIONS))
        self.assertEqual({q[2]["area"] for q in days}, set(plan.REGIONS) | {None})

    def test_watches_come_out_of_verification_not_discovery(self):
        q, w = plan.discovery_queries(TODAY), [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        self.assertEqual(plan.fit_budget([], q, 250), ([], q, 5))
        self.assertEqual(plan.fit_budget(w, q, 250), (w, q, 2))

    def test_low_balance_trims_plan(self):
        q, w = plan.discovery_queries(TODAY), [{"id": "a"}]
        self.assertEqual(plan.fit_budget(w, q, 6), (w, q, 0))
        self.assertEqual(plan.fit_budget(w, q, 4), (w, q[:1], 0))
        self.assertEqual(plan.fit_budget(w, q, 0), ([], [], 0))


def option(airport, price, minutes, flight="DL 100"):
    return {"price": price, "total_duration": minutes,
            "flights": [{"departure_airport": {"id": airport}, "arrival_airport": {"id": "ORD"},
                         "airline": "Delta", "flight_number": flight}]}


class Watches(unittest.TestCase):
    WATCH = {"id": "chicago-oct", "name": "Chicago", "arrival_id": "ORD,MDW", "start": "2026-10-09", "end": "2026-10-12"}
    INSIGHTS = {"lowest_price": 150, "price_level": "typical", "typical_price_range": [140, 260]}

    def body(self, *options):
        return {"best_flights": list(options), "price_insights": self.INSIGHTS,
                "search_metadata": {"google_flights_url": "https://x"}}

    def test_jfk_lga_wins_unless_ewr_is_clearly_cheaper(self):
        row = run.parse_watch(self.body(option("LGA", 200, 150), option("EWR", 185, 150)), self.WATCH, TODAY)
        self.assertEqual((row["airport"], row["price"], row["ewr_saving"]), ("LGA", 200, None))
        row = run.parse_watch(self.body(option("LGA", 200, 150), option("EWR", 150, 150)), self.WATCH, TODAY)
        self.assertEqual((row["airport"], row["price"], row["ewr_saving"]), ("EWR", 150, 50))
        self.assertEqual((row["nyc_price"], row["ewr_price"]), (200, 150))

    def test_detours_and_bare_fares_dont_win(self):
        row = run.parse_watch(self.body(option("JFK", 90, 600), option("LGA", 200, 150)), self.WATCH, TODAY)
        self.assertEqual(row["price"], 200)
        self.assertEqual(row["alt"], {"price": 90, "airport": "JFK", "stops": 0, "minutes": 600})
        # Spirit from EWR at $150 is $210 after the carry-on penalty: not enough to beat LGA at $200.
        row = run.parse_watch(self.body(option("LGA", 200, 150), option("EWR", 150, 150, "NK 5")), self.WATCH, TODAY)
        self.assertEqual(row["airport"], "LGA")

    def test_board_tracks_change_and_lowest(self):
        rows = []
        for back, price in ((2, 210), (1, 180), (0, 195)):
            day = TODAY - timedelta(days=back)
            rows.append(run.parse_watch(self.body(option("LGA", price, 150)), self.WATCH, day))
        board = [b for b in run.build_watches(rows, TODAY) if b["id"] == "chicago-oct"][0]
        self.assertEqual((board["price"], board["change"], board["lowest_seen"], board["days_tracked"]), (195, 15, 180, 3))
        self.assertEqual((board["level"], board["days_out"], board["tier"]), ("typical", 21, None))

    def test_watches_are_in_departure_order(self):
        self.assertEqual([w["id"] for w in plan.active_watches(TODAY)],
                         ["chicago-oct", "phoenix-nov", "toronto-nov", "manila-mar"])

    def test_every_n_days_watch_skips_off_days(self):
        due = [[w["id"] for w in plan.due_watches(TODAY + timedelta(days=d))] for d in range(2)]
        self.assertEqual(sorted(len(d) for d in due), [3, 4])
        self.assertTrue(all("chicago-oct" in d for d in due))

    def test_unchecked_watch_is_pending_and_departed_watch_disappears(self):
        self.assertTrue(all(b.get("pending") for b in run.build_watches([], TODAY)))
        self.assertEqual([b["id"] for b in run.build_watches([], date(2026, 11, 20))], ["toronto-nov", "manila-mar"])


if __name__ == "__main__":
    unittest.main()
