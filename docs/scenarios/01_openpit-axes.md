# 01 — Open-pit axes and geometry

Every sampled axis changes the mine STRUCTURALLY (not just rates):

| axis | range / choices | what it changes |
|---|---|---|
| benches | 6..20 × height {10, 12, 15} m | depth, ramp length, cycle scale |
| berm width | 8..15 m · face angle 60..75° | step-in ⇒ rim size, wall slope |
| rim | superellipse n 1.7..2.6, eccentricity 1..1.9, azimuth, harmonics k=2..4 (a ≤ 0.12) | silhouette — never the same twice |
| phases | 1..3, raised-cosine sector boosts (0.08..0.25) | asymmetric expansions, face placement zones |
| ramp style | spiral / switchback / dual_spiral | network TOPOLOGY class |
| ramp grade · lanes | 8..10% · 1 or 2 | speeds via rimpull · DirectionZones vs free passing |
| shovels | 2..8, deep-weighted (w ∝ bench^1.5), ≤ 2/bench | dispatch problem size, haul-length mix |
| destinations | 1..2 crushers + 1..3 dumps + optional stockpile at 400..2500 m | multi-destination routing |
| surface junctions | 1..3, shared trunk | junction conflicts between streams |
| fleet | 1..3 classes from {777G, 785D, 793F}, sized to MF 0.7..1.5 | heterogeneous rimpull ⇒ bunching |

## Geometry notes

- The polar rim form (superellipse × perturbation) keeps every ring **star-shaped** — simple by
  construction; the floor gate (≥ 40 m radius) rejects pits too deep for their rim.
- Ramps are split into piecewise-CONSTANT-grade segments at build time, so traversal time is
  closed-form. Spiral: wall-hugging helix integrated so one bench height accrues between
  crossings. Switchback: continuous zigzag, each leg starting where the previous turned, every
  180° turn a capacity-1 junction (15 s). Dual-spiral with 1 lane: a one-way circulation PAIR
  (climb ramp / descent ramp), verified by routing tests on disjoint segment sets.
- Faces tie along their bench arc to EVERY ramp crossing on that bench — required for one-way
  circulation to route, and the physical berm road anyway.
- Single-lane spiral/switchback ramps chain one DirectionZone per bench span (`single_lane_op`,
  ADR-0005): opposing traffic arbitrates at the crossings, passing at the berms.
