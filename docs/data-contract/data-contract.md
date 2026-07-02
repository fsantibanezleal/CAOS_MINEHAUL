# Data contracts

Every artifact the package writes or reads, verbatim, with the validation rules and the
edge-case handling. Consumers should treat this folder as normative.

- [01 — cyclelog/v1 (the DispatchLab ingest contract)](01_cyclelog-v1.md)
- [02 — MineSpec, provenance and topography documents](02_spec-provenance-topo.md)

## Outlier / edge handling summary

| situation | behavior |
|---|---|
| payload > 400 t sampled | truncated at the 400 t contract cap (writer) / rejected (validator) |
| final load of a dig block | PARTIAL payload = exactly the remaining tonnes (plan coupling) |
| loading truck at shift cutoff | its drawn tonnes reported as `chute_in_flight_t` (underground) |
| non-monotonic per-truck times | file rejected (validator) |
| illegal event transition | file rejected (validator: per-truck load→haul→dump→return machine) |
| node both shovel and dump | flagged, not rejected (matches the consumer) |
| empirical MF outside [0.4, 2.5] | flagged, not rejected |
