# Units vs weighting (LLM1 → resolver → LLM2)

## What the data actually does

- Ensemble tables filter by `variable` **text code** only (no `units` column on forecast rows).
- Metadata `variables` can have **multiple rows** with the same `variable` code and different
  `units` / `variable_name` (e.g. `temp_2m` as ºC, ºF, and “pop. weighted”). Those rows are
  distinct `variable_id`s for catalog/ACL links; they are **not** separate filters you can
  pass as `variable='temp_2m' AND units='ºF'` on ensemble SQL.
- True alternate **meanings** usually have **different codes**: `temp_2m` vs `temp_2m_gen`,
  `ghi` vs `ghi_gen`, etc.

So:

| User intent | Mechanism | Downstream |
|---|---|---|
| °F instead of °C (same quantity) | **Convert / relabel** stored series | Same `variable`; apply conversion; chart/assumption in preferred unit |
| Population vs capacity weighting | **Select variable code** | Different `values[]` canonical name |

## LLM1 contract (domain)

- Weighting → choose canonical variable (ask if ambiguous).
- Unit preference → `variable.criteria.unit_preference` (e.g. `"°F"`); omit for catalog default.
- Defaults: catalog unit; weighting implied by question (demand → pop; gen context → capacity).
- Visualization `y_axis.unit` / notes should reflect the **preferred** unit when set.

## Resolver (next)

- Resolve canonical variable as today (availability gate unchanged).
- Read `criteria.unit_preference`; normalize aliases (`F`, `degF`, `ºF` → `°F`).
- Attach on `ResolvedVariable`:
  - `unit` = preferred or native
  - `native_unit` = catalog/storage unit
  - `unit_conversion` = `null` | `{ "from": "°C", "to": "°F", "method": "linear" }` (or similar)
- If preferred unit is not convertible from native (unknown pair), clarify — do not invent.
- Catalog hygiene (follow-up): stop collapsing duplicate `variables` rows via last-wins
  `get_variable_units()`; expose one canonical entry with `native_unit` + `convertible_to[]`
  and separate weighting siblings as distinct catalog variables.

## LLM2 / SQL / charts

- SQL still filters `variable = '<canonical>'`.
- If conversion required: either
  - express converted value in SQL (`ensemble_value * 9/5 + 32` for °C→°F), or
  - convert in the result layer; either way REP must carry the conversion intent.
- `chart_details.y_unit` / assumptions = **preferred** unit.
- Existing SQL-prompt line “prefer °C unless user specifies” aligns with this once REP
  carries `unit_preference`.

## Why metadata has multiple units for one `variable` code

`variables` can list the same code (e.g. `temp_2m`) more than once with different `units`
/ display names and different `variable_id`s. That is **catalog / ACL metadata**, not a
second ensemble series keyed by unit:

- Forecast/lake rows filter only by `variable` text (e.g. `temp_2m`); there is no units column.
- Sampled ISO ensemble values for `temp_2m` sit on a Celsius scale (~20–28 in summer).
- The ºF `temp_2m` metadata row is linked only to a couple of niche entities
  (`leeward_pilot`, `rayburn`); most entities link the ºC (and sometimes pop-weighted °C) ids.
- So duplicate unit rows are best read as “this entity’s UI/catalog prefers labeling or
  linking temperature as °F” (or a weighting label), not “query a different stored unit
  series.” Product behavior: one stored series per code; user °F preference → convert.
