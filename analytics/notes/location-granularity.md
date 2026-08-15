# Location granularity (aggregate vs point)

## What `is_aggregate` means

On `locations` only. **Geography**, not “this row forecasts more variable types.”

| Flag | Meaning |
|---|---|
| `true` | Named zone or system portfolio (parent). Weather is often a weighted mix of point stations. |
| `false` | Point site: city, plant-area, weather station (child). |

Energy variables live on `resources`. `location_variables` is weather-only.

## `location_weights`

`parent_location_id` = aggregate; `location_id` = point child; `weight` + input/output variable. Not every aggregate has rows (e.g. some backcast zones).

## v2 catalog vs full table

`load_entity_catalog` keeps `is_aggregate = true OR resource_type = 'portfolio'` so LLM1 examples and default “what locations?” lists stay named zones.

Point sites still exist. Counts go in `location_types[].aggregation`. Names are loaded on demand (`load_entity_point_resources`, `load_location_composition`) or via `resolve_location` when the user names a city.

## LLM1 criteria

- `location.criteria.granularity`: `aggregate` (default) / `point` / `both`
- `location.criteria.composition`: true + zone name in `values` → weight-table children

## Resolver / LLM2

Metadata plans inject the `location_weights` schema slice. Forecast SQL uses the resolved `weather_sims_id` / `energy_sims_id` whether the place is a zone or a point. Named-place miss on the aggregate catalog already falls back to `metadata_db.resolve_location` (includes point sites).
