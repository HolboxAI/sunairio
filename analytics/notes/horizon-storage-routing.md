# Horizon / storage routing notes (resolver + LLM2)

Internal engineering notes from domain confirmation (2026-08). Do **not** paste table
names into `llm1-consultant.md`; LLM1 stays business-horizon only. Resolver and LLM2 /
SQL generation own physical routing.

## Products vs user-facing horizons

| User horizon (LLM1) | `ensemble_runs` window | Typical refresh | Valid reach (approx.) |
|---|---|---|---|
| Short range | `forecast` | hourly | init → +336h (14d) |
| Seasonal / mid | weather `seasonal`; energy uses `base` for mid+long | ~weekly (168h) | weather seasonal ~months hot, ~2–3y in lake; energy mid ~3mo hot then long |
| Long range | `base` | weather: irregular/months; energy `base`: ~weekly | out to ~2050 |

Path alignment holds across weather / energy / market, windows, and entities.

## Forecast DB vs Lake (do not conflate)

### Short-range `forecast` product

- Hot path: Forecast DB (`weather_forecast_ensemble_short` / `_extended`, `energy_forecast_ensemble`, market forecast tables).
- Cold path: Lake archived forecast tables (`glue.sunairio.*_forecast_ensemble`).
- **Freshness:** Lake’s *latest* short-range init trails Forecast DB by **~69–72h (~3 days)**. Matches “use Lake when init is ≥ ~3 days old.”
- Measured with metadata candidate inits + `ensemble_path = 1` point lookups (avoid full-table `MAX` scans).
- Archival is **per initialization**, not per variable. Lag is the same across variables for an entity; some vars may be missing on one side at a given init (coverage gap ≠ different lag).

### Weather short vs extended (hot only)

- `_short`: hourly valids, init → init+18h; published on hourly inits.
- `_extended`: init+18h → +336h; published on UTC 6h grid (00/06/12/18 UTC —
  **not** entity local time). Use floored `forecast_long` init when spanning
  beyond +18h.
- **Publication lag:** hourly short can reach e.g. 07:00 UTC before extended
  `06:00` lands. Resolver floors to the 6h grid, then **walks back** on that
  grid (probe `weather_forecast_ensemble_extended`, `ensemble_path=1`) until
  rows exist — e.g. short `07:00` → floor `06:00` empty → use `00:00`.
- **Entity cadence exceptions:** ISONE extended may land ~once/day (~12:00 UTC);
  walk-back within 24h still finds the prior landed anchor.
- Lake archived `weather_forecast_ensemble` observed as **hourly for the full ~14d** for sampled ERCOT path-1 rows (hot short/extended split is a Forecast-DB concern).

### Seasonal product (senior confirmation + DB check)

- `weather_seasonal_ensemble` in Forecast DB **and** `glue.sunairio.weather_seasonal_ensemble` in Lake are updated **on the same weekly write**, before metadata flips — **no seasonal freshness issue** choosing Lake.
- Same seasonal `initialization` can exist in both; **valid span differs**:
  - Forecast DB seasonal (ERCOT example): ~init → ~end of +3–4 months (hot mid-horizon).
  - Lake seasonal (same init): calendar span ~multi-year (e.g. 2026→2028), ~hourly valids.
- For “Sep–Oct every year until 2050”: Lake seasonal for nearer years is fine for freshness; **base** (`glue.sunairio.weather_base_ensemble`) still required for the 2050 tail.

### Energy naming asymmetry

- Energy has **no** `seasonal` window in `ensemble_runs`; mid-horizon + long live under `base`.
- Hot mid: `energy_base_ensemble`; Lake `glue.sunairio.energy_base_ensemble` holds mid **and** out to 2050 (filter by `valid_datetime`).

## `ensemble_runs` (Metadata DB) — use first

Registry columns that matter: `entity_id`, `ensemble_type`, `ensemble_window`, `initialization`, `active`, `complete`, optional `properties` (`simYears` on some weather seasonal/base).

App latest init: `MAX(initialization) WHERE active AND complete` per entity × type × window.

Windows present: `forecast`, `seasonal`, `base`, `balmo`, plus `backcast` / `reforecast` / `reforecast_base`.  
Types in use: `weather`, `energy`, `fundamental_market`.

## Resolver / LLM2 implications (TODO wiring)

1. **Horizon class from timeframe** — map user range to forecast / seasonal / base (and market balmo) before picking tables; multi-year + 2050 ⇒ seasonal ∪ base, not forecast-only.
2. **Init selection** — always from `latest_inits` / `ensemble_runs`; weather
   short+extended beyond 18h ⇒ `forecast_long` (UTC 6h floor + walk-back probe
   on extended table when the floored anchor has not landed yet).
3. **Backend switch** — short-range (and hot seasonal/base mid) Forecast DB if init younger than ~3 days, else Lake; seasonal freshness is not the reason to prefer Forecast DB over Lake.
4. **Non-overlapping `valid_datetime` predicates** across tier UNION ALL branches; energy skips weather-style lake-seasonal tier.
5. **Probe discipline** — when validating cadences/horizons in prod data: last ~10–12 inits, `ensemble_path = 1`, one location/variable; never full-table scans for `MAX(initialization)`.

## Open checks (still useful later)

- Confirm hot Forecast-DB `_extended` 6h valid step across entities/variables.
  **Confirmed 2026-08-12:** ERCOT/PJM/MISO/PSCO publish on UTC 00/06/12/18;
  ISONE ~daily 12:00 UTC. Walk-back required during lag window.
- Confirm hot `weather_seasonal_ensemble` / `energy_base_ensemble` valid step and exact +336h → ~3mo bounds per entity.
- Market: lake tables have **no** `location` column (hub embedded in `variable`); balmo ~daily.
- Not every entity publishes every window (e.g. Duke energy forecast sparse; market mostly ERCOT/PJM).
