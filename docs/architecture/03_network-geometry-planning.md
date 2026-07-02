# 03 — Network, geometry and the planning overlay

## The constrained road network

A directed multigraph of `Segment`s between `NodeSite`s. Every operational constraint real
mines run under is an edge attribute the router and the traffic layer respect:

| attribute | routing effect | runtime effect |
|---|---|---|
| `one_way` | adjacency only a→b | — |
| `width_class` | unit wider than segment ⇒ inadmissible | — |
| `single_lane_op` | — | zone membership legal despite width 2 (ADR-0005) |
| `zone_id` | optional cost prior | DirectionZone arbitration (lockout / loaded_priority / group_batching) |
| `grade_pct` (signed) | speed via rimpull/retarder | traversal time |
| `speed_limit_kmh` + caps | MIN-composed | MIN-composed |
| length / headway | — | slot capacity `floor(len/80 m)` + no-overtake exit rule |

## Geometry builders

Pure functions of frozen designs (`OpenPitDesign`, `UndergroundDesign`): rings/ramps/faces or
levels/decline/drifts/passes, plus the derived network and traffic specs. All sampling lives in
the scenario generators; a builder given the same design always returns the same geometry, and
unbuildable designs raise typed errors the generators treat as resample signals.

## The planning overlay (open pit)

`PitState` owns mutation: legal depletion (6 named rules), bench-completion cascade, slope
damage, speed zones. It exposes a two-tier view to the sim: cheap `routing_inputs()`
(closures + caps, every quote) and a full `effective_network()` rebuild only when
`overlay_revision` bumps. In-flight traversals always finish on the geometry they were quoted
on; completed benches keep a LEGACY spur (the physical road to a mined-out bench remains until
the next pushback consumes it) — both semantics were field-found by the plan-coupled fire test.
