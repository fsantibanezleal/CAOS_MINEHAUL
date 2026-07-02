# 03 — Bunching, headway and direction-zone policies

## Bunching must EMERGE

Fleet heterogeneity and payload variance make trucks run at different speeds; on a no-passing
haul road, fast trucks pile up behind slow ones and fleet productivity drops (Soofastaei 2016).
Most simple simulators SAMPLE this loss from a distribution. Here it emerges from two rules:

1. **Slot capacity**: a segment holds `max(1, floor(length / 80 m))` vehicles per direction;
   entry blocks when full (FIFO wakeup).
2. **No-overtake**: `exit_t = max(own_kinematic_exit, predecessor_exit + 8 s)` per direction —
   a fast empty truck exits no earlier than 8 s behind a slow loaded one.

The U6b test shows consecutive dumps arriving ≥ 8 s apart on a shared segment under traffic and
closer in free-flow (`fast_mode`) — same seed, so the delta IS the traffic.

## Direction zones (single-lane roads)

A DirectionZone is a stretch where opposing traffic cannot meet: an underground drift between
passing bays, a narrow pit ramp span (ADR-0005). Arbitration policies (the classic set from
underground traffic-simulation studies, cf. Queen's 2016):

| policy | rule | when it wins |
|---|---|---|
| `lockout` | strict direction mutual exclusion, FIFO between groups | simple, fair, baseline |
| `loaded_priority` | direction flips only when no LOADED vehicle still waits upstream | production climbs beat empty returns: throughput ≥ lockout |
| `group_batching` | hold direction until k pass or a timer expires | long zones where flip overhead dominates |

The package reproduces the qualitative ordering as a TEST
(`test_zone_policy_ordering_loaded_priority_vs_lockout`): on a reference underground spec with
opposing decline traffic, `loaded_priority` tonnage ≥ `lockout` tonnage.

## Junctions

A capacity-k conflict point: crossing holds the junction `cross_s` seconds, FIFO by arrival —
also how switchback 180° turns (capacity 1, 15 s) and underground zigzag turns are modeled.
