# Historical actuals (observations)

Confirmed domain mapping for resolver / LLM2. Keep table names out of LLM1; LLM1 only
needs: energy + price actuals exist; weather actuals do not yet.

## Storage (Metadata DB — not “metadata” content)

Despite living in the Metadata DB, these tables hold **observed** time series.

### `historical_iso_load_gen` — energy actuals

| Column | Maps to |
|---|---|
| `iso` | `entities.entity` (e.g. `ERCOT`, not shortname) |
| `region` | `resources.energy_sims_id` |
| `variable` | `variables.variable` |
| `hour_beginning` | Hour beginning timestamp |
| `hour_value` | Observed value |

No referential integrity to entities/resources/variables today (legacy); treat mappings as
logical. Refactor to add RI later.

### `historical_iso_prices` — price actuals

| Column | Maps to |
|---|---|
| `iso` | `entities.entity` |
| `region` | `markets.market_sims_id` (approx.; hubs — do not invent ids) |
| `hour_beginning` | Hour beginning timestamp |
| day-ahead / real-time | Two value columns (DA and RT) |

## Weather actuals

Not in-platform yet. APIs to be added later. Do not invent a weather actuals table.
Weather “history” today can only mean a past **forecast** initialization if the user accepts that framing.

## Routing reminders

- `historical` intent → energy/price actuals tables; `initialization` usually `none`.
- Forecast-vs-actual comparisons need both ensemble (past or aligned init) and actuals.
- Schema select already distinguishes `historical_iso_load_gen` vs weather historical stubs.
