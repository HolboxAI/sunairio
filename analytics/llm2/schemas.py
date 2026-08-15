"""Physical schema slices for analytics LLM2 (Metadata + Forecast).

Lake / Glue DDL is intentionally empty — stubbed until that backend is enabled.
Logical names match resolver ``required_schema`` entries.
"""

from __future__ import annotations

from typing import Dict, List

# ── Metadata DB ──────────────────────────────────────────────────────────────

_ENTITIES = """
### entities (Metadata DB)
| Column | Type | Notes |
|---|---|---|
| entity_id | uuid/text | Primary key |
| entity | text | Display name, e.g. ERCOT, PJM |
| shortname | text | Forecast project_name, e.g. ercot_generic |
| timezone | text | e.g. US/Central |
| is_iso | boolean | |
| has_forecast | boolean | |
"""

_LOCATIONS = """
### locations (Metadata DB)
| Column | Type | Notes |
|---|---|---|
| location_id | uuid/text | |
| location_name | text | |
| weather_sims_id | text | Weather ensemble `location` |
| timezone | text | |
| is_aggregate | boolean | true = named zone / RTO parent geography; false = point site (city, station, plant area). Not “more variable types”. |
"""

_RESOURCES = """
### resources (Metadata DB)
| Column | Type | Notes |
|---|---|---|
| resource_id | uuid/text | |
| resource_name | text | |
| energy_sims_id | text | Energy ensemble `location` / historical `region` |
| entity_id | uuid/text | FK entities |
| location_id | uuid/text | FK locations |
| resource_type_id | uuid/text | FK resource_types |
"""

_RESOURCE_TYPES = """
### resource_types (Metadata DB)
| Column | Type | Notes |
|---|---|---|
| resource_type_id | uuid/text | |
| resource_type | text | portfolio, load, zone, wx_zone, solar_zone, wind_zone, cdr_zone, solar, wind, … |
"""

_LOCATION_WEIGHTS = """
### location_weights (Metadata DB)
How an **aggregate** location is mixed from **point** children. Use for composition
questions (“what stations make up Houston Load Zone?”), not for ensemble values.

| Column | Type | Notes |
|---|---|---|
| weight_id | uuid | |
| location_id | uuid | Child **point** site (`locations.is_aggregate = false`) |
| parent_location_id | uuid | Parent **aggregate** zone (`is_aggregate = true`) |
| input_variable_id | int | FK variables — field on the child |
| output_variable_id | int | FK variables — field on the parent (often the same name; sometimes ghi→ghi_gen) |
| weight | numeric | Share of the parent; static recipes often sum to 1.0 per output variable |
| is_dynamic | boolean | true → capacity/time-varying weights (`location_dynamic_weights`) |

Join: `locations` as parent on `parent_location_id`, child on `location_id`.
Scope to an entity via `resources.location_id = parent.location_id`.
Do not invent children; if no rows, say the zone has no stored recipe.
"""

_LOCATION_VARIABLES = """
### location_variables (Metadata DB)
Weather variables forecasted at a **location** (aggregate or point).

| Column | Type | Notes |
|---|---|---|
| location_id | uuid | FK locations |
| variable_id | int | FK variables |

Join `locations` + `variables`. Filter `locations.is_aggregate` when the REP
asks for zones vs point sites. Do not invent variables; empty join means that
place has no weather vars.
"""

_RESOURCE_VARIABLES = """
### resource_variables (Metadata DB)
Energy variables forecasted at a **resource** (usually zone / portfolio).

| Column | Type | Notes |
|---|---|---|
| resource_id | uuid | FK resources |
| variable_id | int | FK variables |

Join `resources` + `variables` (+ `resource_types`). Point cities/farms often
have no energy rows — that is valid, not a missing table.
"""

_VARIABLES = """
### variables (Metadata DB)
| Column | Type | Notes |
|---|---|---|
| variable_id | uuid/text | |
| variable_type | text | Weather / Energy / … |
| variable | text | Canonical code used in ensemble + historical filters |
| variable_name | text | Display name |
| units | text | |
"""

_HISTORICAL_LOAD_GEN = """
### historical_iso_load_gen (Metadata DB — energy actuals)
Observed hourly energy history. Not an ensemble.

| Column | Type | Maps to |
|---|---|---|
| iso | text | entities.entity (e.g. ERCOT, PJM) |
| region | text | resources.energy_sims_id |
| variable | text | variables.variable (e.g. load) |
| hour_beginning | timestamptz | Hour beginning |
| hour_value | double | Observed value |

Filter with iso + region + variable + hour_beginning range.
No weather actuals table exists — do not invent one.
"""

_HISTORICAL_PRICES = """
### historical_iso_prices (Metadata DB — price actuals)
| Column | Type | Notes |
|---|---|---|
| iso | text | entities.entity |
| region | text | markets.market_sims_id |
| hour_beginning | timestamptz | |
| day_ahead | double | Day-ahead price |
| real_time | double | Real-time price |
"""

# ── Forecast DB (hot path) ───────────────────────────────────────────────────

_ENSEMBLE_COLS = """
Common ensemble columns (Forecast DB PostgreSQL):
| Column | Type | Notes |
|---|---|---|
| initialization | timestamptz | Forecast run time |
| project_name | text | entities.shortname |
| location | text | weather_sims_id or energy_sims_id |
| variable | text | variables.variable |
| valid_datetime | timestamptz | Forecast valid hour (HB) |
| ensemble_path | int | 0–999 |
| ensemble_value | double | Path value |
"""

_WEATHER_FORECAST = f"""
### Weather forecast tables (Forecast DB)
{_ENSEMBLE_COLS}

| Table | valid_datetime range relative to initialization |
|---|---|
| weather_forecast_ensemble_short | init → init + 18h |
| weather_forecast_ensemble_extended | init + 18h → init + 336h |
| weather_seasonal_ensemble | init + 336h → ~3 months |

There is **no** bare `weather_forecast_ensemble` table on Forecast DB — expand to
`_short` + `_extended` (UNION ALL) when the horizon spans both.
Use the REP's resolved initialization timestamp(s); do not invent them.
"""

_ENERGY_FORECAST = f"""
### Energy forecast tables (Forecast DB)
{_ENSEMBLE_COLS}

| Table | valid_datetime range relative to initialization |
|---|---|
| energy_forecast_ensemble | init → init + 336h |
| energy_base_ensemble | init + 336h → ~3 months |

UNION ALL with non-overlapping bounds when the horizon spans both tiers.
Use energy_sims_id as `location`. project_name = entity shortname.
"""

_MARKET_FORECAST = f"""
### Market / fundamental price tables (Forecast DB)
{_ENSEMBLE_COLS}

| Table | Role |
|---|---|
| fundamental_price_forecast_ensemble | Near-term |
| fundamental_price_balmo_ensemble | Balance-of-month |
| fundamental_price_base_ensemble | Longer base |

`location` maps to market_sims_id. Prefer these only when the variable is a price.
"""

# Lake stubs — content intentionally omitted for this phase.
_LAKE_STUB = """
### Data Lake (NOT ENABLED in this phase)
Queries against `glue.*` / Arrow Flight are **out of scope**.
Do not emit Lake SQL. If the REP would require archived/cold storage only,
return target `"unsupported"` with sql null and explain in assumptions.
"""

_HISTORICAL_WEATHER_STUB = """
### historical_weather
**Not available.** There is no in-platform weather actuals table.
Do not invent one. Prefer clarification via assumptions if the REP asked for it.
"""

# Logical required_schema name → markdown slice(s)
SCHEMA_SLICES: Dict[str, str] = {
    "entities": _ENTITIES,
    "locations": _LOCATIONS,
    "resources": _RESOURCES,
    "resource_types": _RESOURCE_TYPES,
    "location_weights": _LOCATION_WEIGHTS,
    "location_variables": _LOCATION_VARIABLES,
    "resource_variables": _RESOURCE_VARIABLES,
    "variables": _VARIABLES,
    "historical_iso_load_gen": _HISTORICAL_LOAD_GEN,
    "historical_iso_prices": _HISTORICAL_PRICES,
    "historical_weather": _HISTORICAL_WEATHER_STUB,
    "weather_forecast": _WEATHER_FORECAST,
    "energy_forecast": _ENERGY_FORECAST,
    "fundamental_market_forecast": _MARKET_FORECAST,
    "forecast_archive": _LAKE_STUB,
    "lake": _LAKE_STUB,
}

# Always append when any forecast table is injected
FORECAST_ROUTING_HINT = """
### Forecast DB routing hints (this phase — hot path only)
- Prefer Forecast DB tables listed above. Do **not** use `glue.*` Lake tables.
- Weather tier-1 spanning short+extended: UNION ALL with non-overlapping
  valid_datetime predicates. Use `initialization.resolved` on
  `weather_forecast_ensemble_short` and `initialization.resolved_extended`
  (UTC 6h grid, walk-back resolved) on `weather_forecast_ensemble_extended`.
  Never reuse the hourly short init on the extended table.
- Energy spanning forecast+base: UNION ALL at init + 336 hours boundary.
- Filter: project_name, location, variable, initialization, valid_datetime range
  from the REP. Percentile / probability / mean are computed in SQL over
  ensemble_path (0–999).
- Timestamptz arithmetic: cast literals before `+ INTERVAL`, e.g.
  `'2026-08-12T08:00:00Z'::timestamptz + INTERVAL '18 hours'`.
"""

METADATA_ROUTING_HINT = """
### Metadata / historical routing hints
- Catalog lookups use entities / locations / resources / variables /
  location_weights / location_variables / resource_variables.
- Weather inventory is on `location_variables`; energy inventory is on
  `resource_variables`. `locations.is_aggregate` distinguishes zone vs point site.
- `locations.is_aggregate = true` is a parent zone or portfolio; `false` is a point site.
  Ensemble `location` for weather is `weather_sims_id` (either kind). Default user-facing
  lists are aggregate zones; point sites and weight recipes are first-class when asked.
- Observed energy actuals use historical_iso_load_gen (iso, region, variable,
  hour_beginning, hour_value).
- Price actuals use historical_iso_prices.
- PostgreSQL dialect only.
"""


def slices_for(required_schema: List[str]) -> List[str]:
    """Return ordered unique schema markdown slices for the given logical names."""
    out: List[str] = []
    seen = set()
    for name in required_schema or []:
        key = (name or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        body = SCHEMA_SLICES.get(key)
        if body:
            out.append(body.strip())
        else:
            out.append(
                f"### {key}\nNo dedicated slice registered. Use only tables from "
                "other injected schemas; do not invent DDL."
            )
    return out
