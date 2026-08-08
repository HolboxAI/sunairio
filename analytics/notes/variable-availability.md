# Variable availability (LLM1 injection + resolver gate)

## Injection (menu)
`build_llm1_injection` loads `metadata_db.load_entity_variables(entity_ids)` and builds
`variable_catalog` with `allowed_names` = union of linked vars across the user's entities
(`resource_variables` ∪ `location_variables`). LLM1 only sees vars it could ever ask for.

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
Enforcement stays in injection + resolver gate.
