# 03 — Validity gates and diversity

`generate_*` resamples (fresh named substreams per attempt) until a spec passes ALL gates, or
raises `GenerationError` NAMING the failing checks after 25 attempts — a degenerate scenario can
never ship silently. Order: cheap structural gates first, the smoke simulation last.

| gate | rejects |
|---|---|
| `contract_ready` | shovel ids not 1..N, dump ids < 101, truck ids not contiguous, class able to exceed 400 t |
| `connectivity` | any (truck class × loader × dump) without BOTH a loaded route and an empty return, on the constrained graph |
| `grades` | any segment beyond its kind limit (open pit 11%, underground 16%) |
| `geometry_sane` | floor radius < 40 m; elevations outside [-depth, 0]; UG: < 3 levels, passes without LHDs |
| `traffic_sane` | any DirectionZone ≥ 450 m (a bay at least that often); junction degree > 5 |
| `throughput_sane` | static MF outside [0.5, 2.2]; est. cycle outside (6–90 min pit / 2–90 min UG) |
| `deadlock_free_smoke` | a smoke run (horizon = max(30 min, 2.5 × est. cycle) so deep mines are not false-failed) that deadlocks, yields < 8 rows, leaves a loader unserved, or completes no cycle |

The validator itself never crashes on malformed documents — unknown nodes, missing segments and
the like come back as NAMED failures (a validator that throws is a validator that gets bypassed).

## Diversity signatures

Batch mode (`ensure_diverse=True`) fingerprints each spec —
`(kind, ramp/access style, depth bucket, shovels, dumps, junctions, network-size bucket,
flow mode)` — and regenerates under bumped child seeds until the signature is unique within the
batch. The committed [gallery](../../gallery/README.md) is the visual check on top of the
structural one; the U9 review confirmed twelve visibly different pits.
