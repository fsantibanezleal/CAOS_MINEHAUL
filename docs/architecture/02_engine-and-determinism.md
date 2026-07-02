# 02 — The engine and determinism

## The engine

A binary heap of `(time, seq, callback, args)`. `seq` is a monotone counter, so equal-time events
pop FIFO on every platform — the detail that makes cross-OS byte-identity possible. Cancellation
is by tombstone (no heap surgery); scheduling into the past raises; a run that can no longer
progress raises `SimulationDeadlock` instead of spinning.

## The three determinism rules

1. **One randomness source.** `RngManager(seed)` derives independent named child streams via
   `SeedSequence((seed, *name.bytes))` — `payload`, `loadtime`, `dumptime`, `policy`, `init`,
   `lhd.dig`, `lhd.bucket`, `fail.truck`, `fail.loader`, and per-attempt generator streams.
   Nothing else may draw randomness; physics modules take no RNG at all.
2. **Deterministic tie-breaking everywhere.** Adjacency sorted at freeze; Dijkstra ties break on
   `(cost, seq)`; wait queues are FIFO deques; batch child seeds derive from named streams.
3. **Rounding only at the boundary.** The engine clock is never rounded; cyclelog times are
   re-zeroed and rounded to 0.1 s at export only.

Consequence, asserted in CI on ubuntu AND windows: same `(spec, policy, seed)` ⇒ identical event
list ⇒ identical CSV bytes.

## Performance envelope

U11 records ~77k executed events/s on the starter preset and ~63k on a 48-truck pit (traffic
on), with a 20k ev/s floor enforced by `tests/test_perf.py` in CI. Hot paths are dictionary
lookups: the `SpeedSolver` memoizes per `(class, GVW bucket, grade, rr, limit)`, and the route
cache keys CONTAIN the closure/cap state, so invalidation is automatic (a changed closure set is
simply a different key).
