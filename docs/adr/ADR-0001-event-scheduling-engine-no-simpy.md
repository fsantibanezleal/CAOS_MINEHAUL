# ADR-0001 — Hand-rolled event-scheduling engine (no simpy)

- Status: Accepted · Date: 2026-07-02 (recorded at U12; decision made at U5)

## Context

The package promises **byte-identical runs across OS and sessions** — the property every
downstream artifact (cyclelog exports, gallery, tests) is built on. Generator-based frameworks
(simpy) schedule through Python generators and wall-ordering details that make cross-version
determinism harder to guarantee, add a dependency, and hide the event queue from profiling.

## Decision

A ~100-line event-scheduling core: a heap of `(time, seq, callback, args)` with a monotone
sequence number (equal-time FIFO on every platform), tombstone cancellation, rejection of
past-time scheduling, and a `SimulationDeadlock` signal. All waiting/queueing is modeled by
explicit resource objects (`QueueResource`, `SlotResource`, `DirectionZoneResource`), not
coroutines.

## Consequences

- Determinism is a property of the data structure, not of framework internals; the same
  `(spec, policy, seed)` reproduces event-for-event on ubuntu and windows in CI.
- No behavioral dependency; numpy stays the only runtime requirement.
- Agent logic is continuation-passing (callbacks) rather than coroutine style — slightly more
  verbose, fully explicit.
- Measured throughput ~60–80k executed events/s on reference scenarios (U11), far above the
  20k floor the CI enforces.
