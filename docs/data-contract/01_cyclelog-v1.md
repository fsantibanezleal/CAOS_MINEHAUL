# 01 — cyclelog/v1

CSV, UTF-8, LF line endings, header EXACTLY:

```
t,truck_id,shovel_id,event,payload_t
```

| column | meaning |
|---|---|
| `t` | float seconds, re-zeroed so the first row is 0.0, ONE decimal (rounded at export only) |
| `truck_id` | int, 1..N stable per roster |
| `shovel_id` | loader node for `load`/`haul`; dump node for `dump`/`return` |
| `event` | `load → haul → dump → return`, legal per truck; rows globally time-sorted |
| `payload_t` | 0 for `load`/`return`; loaded tonnes (1 decimal, ≤ 400) for `haul`/`dump` |

## Event anchoring (ADR-0003, the semantics decision)

Events mark the START of their phase, so consumer deltas mean: `t_haul−t_load` = loading,
`t_dump−t_haul` = loaded travel, `t_return−t_dump` = dumping, `t_nextload−t_return` = empty
travel + queue.

## Id mapping

Open pit: faces are shovels `1..N`; crushers/dumps/stockpile are `101..`. Underground: whatever
the TRUCK fleet loads at is the shovel — chutes (LHD flows) or LHD-loading drift stubs
(truck_direct); the surface dump / shaft bin is the dump id.

## The validator

`minehaulsim.io.validate_cyclelog` is a faithful Python port of the consumer's ingest checks
(header, numeric fields, event legality per truck, monotone times, payload bounds, minimum row
count, shovel/dump presence, both-roles flag, empirical-MF flag). It gates every artifact in the
tests AND the CLI, so a file that leaves this package has passed the SAME rules DispatchLab
applies on ingest. Exit code 1 from `minehaulsim validate <file>.csv` means the consumer would
reject it.
