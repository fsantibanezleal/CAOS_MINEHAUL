# ADR-0003 — cyclelog/v1 event anchoring (the DispatchLab semantics decision)

- Status: Accepted · Date: 2026-07-02 (recorded at U12; decision locked at U7)

## Context

DispatchLab's `EmpiricalBlock` estimates per-mine distributions from event DELTAS in the
cyclelog. If minehaulsim anchored events differently than the consumer assumes, every derived
statistic (loadMean, travel medians, dump mean) would be silently wrong while the file still
validated. This is the classic contract trap: consistent-looking, false.

## Decision

Events mark the **START of their phase**, and the consumer's deltas therefore mean:

| delta | meaning |
|---|---|
| `t_haul − t_load` | loading service time (loadMeanSec) |
| `t_dump − t_haul` | loaded travel (fullTravelMedian) |
| `t_return − t_dump` | dumping service (dumpMean) |
| `t_nextload − t_return` | empty travel + queueing (emptyTravelMedian + queue) |

Concretely: `load` = loading service START, `haul` = departure loaded (loading COMPLETE),
`dump` = dumping service START, `return` = departure empty. Times are engine-exact and only
rounded (0.1 s) + re-zeroed at the export boundary. `run_shift` emits exactly these semantics;
`io/cyclelog.py` documents them; the consumer-side integration re-checks them (U13).

## Consequences

- Queue time is folded into the empty-travel delta (matching the consumer's estimator); the
  simulator's own KPIs keep the separated accounting.
- Any future event (e.g. `spot`) needs a schema bump (`cyclelog/v2`), never a reinterpretation.
