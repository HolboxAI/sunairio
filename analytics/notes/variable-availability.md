# Variable availability (LLM1 injection + resolver gate)

## Injection (menu)
`build_llm1_injection` loads `metadata_db.load_entity_variables(entity_ids)` and builds
`variable_catalog` with `allowed_names` = union of linked vars across the user's entities
(`resource_variables` ∪ `location_variables`). LLM1 only sees vars it could ever ask for.
It does **not** get a per-place matrix. `location_model` tells it to plan a named-place
metadata lookup instead of reciting the entity catalog.

## Per-place lookup (metadata answer)
`load_variables_for_locations(shortname, names)` unions:

- **Weather** — `location_variables` on `locations` (aggregate or point)
- **Energy** — `resource_variables` on `resources` (usually zone / portfolio)

Names match `location_name`, `resource_name`, or sims ids. Follow-ups use the session
`catalog_location_list`. LLM2 metadata plans also get those join tables in schema slices.

## Resolver gate (bouncer)
Pipeline order: entity → **location** → **variable** → …

`entity_variables` on the resolver context (from `_resolver` payload):

- Entity check: canonical var must be in that entity's `variables` list.
- Resource-type check (after locations resolve): energy vars must intersect
  `energy_by_resource_type[var]` with resolved location `resource_type`s.
- Weather vars (in `weather` list) skip the resource-type check.

Empty `entity_variables` disables the gate (unit tests / legacy callers).

## Prompt
`llm1-consultant.md` states that the injected catalog is entity-scoped and that
location/resource-type fit is still required — no hardcoded variable family lists.
Named-place "what is forecasted at X" must not dump `variable_catalog`.
Enforcement stays in injection + resolver gate + metadata lookup.
