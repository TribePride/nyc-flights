# Research: flight price sources and what counts as a deal

Researched 2026-09-18.


### The read
The hobby-tier flight API market collapsed this year. The only self-serve source with real US prices is Google Flights via a SERP API. The controlling constraint is the 250-call monthly budget, so the design is a funnel: few wide calls, then a handful of narrow ones.

### Aggregation options (known facts)
| Source | Status | Verdict |
|---|---|---|
| Amadeus Self-Service | Decommissioned 17 Jul 2026; enterprise only now | Dead |
| Kiwi Tequila | Closed to self-serve since May 2024; affiliate path needs ~50k MAU | Dead for us |
| Skyscanner | Partner-only; RapidAPI wrappers ~20 req/month free | Dead for us |
| Duffel | Self-serve, but it's a booking API with look-to-book expectations | Wrong tool |
| Travelpayouts Data API | Free, 300 rpm, but prices are a 7-day cache of Aviasales user searches, segmented by market; US-market coverage is thin | Backup only |
| **SerpApi `google_travel_explore`** | One call = cheapest destinations from `departure_id` (comma-separated airports OK), filters for `stops`, `max_price`, `travel_duration`, `month` | **Discovery** |
| **SerpApi `google_flights`** | Returns itineraries plus `price_insights`: `lowest_price`, `price_level`, `typical_price_range`, `price_history` | **Verification** |
| fast-flights (DIY scraper) | Works, $0, brittle, against Google ToS, no Explore | Fallback if SerpApi budget hurts |

Critical lane: Google sued SerpApi in Dec 2025. The copyright/DMCA claims were dismissed July 2026; Google amended Aug 10, SerpApi moved to dismiss again Aug 25. Unresolved. Risk for us is the vendor disappearing, not liability, so keep the fetcher behind one small interface.

### What counts as a good price (mechanism)
- Going (Scott's Cheap Flights) grades deals **per route, in standard deviations below that route's mean**. Their average sent deal is ~2.2σ below; "Top 15%" ≤ −2.6σ, "Top 10%" ≤ −2.8σ, "Top 1%" ≤ −3.64σ. Absolute price is irrelevant; $250 to Denver can be a worse deal than $420 to Lisbon.
- Inference: a route-relative baseline is the right shape. Our problem is cold start (no history on day 1). Google's `typical_price_range` and `price_history` are a borrowed baseline that fixes that for free.
- Checked on the first real runs (2026-09-18): Explore does not say which origin airport a fare uses when given `JFK,LGA` (the verification call does); one Explore call returns 40-60 destinations; with no `arrival_area_id` it only covers North America, so other regions need their own calls; Explore prices matched the Flights endpoint exactly; `price_insights` was present on 10 of 10 verifications. Of those 10, 2 came back "low" and none were 20% under typical, which is the expected base rate for a strict deal bar.

### Heuristic (v1)
1. **Candidate score** (every Explore row, no API cost): robust z = (price − median) / (1.4826·MAD) over our trailing 90 days for that destination. With <14 observations, fall back to ranking by residual from a log-log price-vs-distance curve fitted to the day's observations. (Plain cents-per-mile was tried first and just picked the longest routes in each bucket.)
2. **Fare hygiene**: ≤1 stop; add a $60 penalty to ULCC fares (Spirit, Frontier, etc.) before comparing, and badge them "bare fare".
3. **Verify** top N candidates with `google_flights`. Tier on the verified price:
   - **Good**: `price_level == "low"` and price ≤ typical low
   - **Great**: ≥20% under typical low, or z ≤ −2.2
   - **Rare**: ≥35% under typical low, or z ≤ −2.6
   Only Good+ is shown; Great/Rare sort first.
4. **EWR gate**: an EWR deal appears only if it is Great+ **and** beats the best JFK/LGA price to the same destination that day by both ≥15% and ≥$40. Otherwise hidden. JFK/LGA win ties.

### What would change my mind
- `price_insights` missing on >30% of verifications → lean on own z-score sooner, tiers based on Explore history only.
- 8 calls/day surfaces <2 deals a week → upgrade to $25 Starter (1,000/mo) or add fast-flights for the verification step.
- SerpApi loses the amended suit or drops the Flights engines → swap fetcher to fast-flights.

### Sources

- Amadeus shutdown: https://tripgic.com/playbook/amadeus-api-shutdown-migration
- Kiwi Tequila access: https://phptravels.com/blog/comprehensive-guide-to-flights-api-integration
- Travelpayouts cache/market limits: https://support.travelpayouts.com (Aviasales Data API), https://github.com/mauriciabad/flights/issues/41
- SerpApi: https://serpapi.com/google-travel-explore-api, https://serpapi.com/google-flights-price-insights, https://serpapi.com/pricing
- Google v. SerpApi: https://theregister.com/2026/02/21/serpapi_google_scraping_lawsuit, https://seroundtable.com (dismissal), https://androidheadlines.com (amended complaint)
- Going grade methodology: https://going.com/guides/going-deal-scores
- fast-flights: https://github.com/AWeirdDev/flights
