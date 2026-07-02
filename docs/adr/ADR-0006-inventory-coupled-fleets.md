# ADR-0006 — Underground fleets couple ONLY through material inventories

- Status: Accepted · Date: 2026-07-02 (decision made at U10)

## Context

Underground mines run two fleets (LHDs on production levels, trucks on the haulage circuit)
whose interaction is the interesting phenomenon: chute starvation when LHDs lag, LHD blocking
when a pass fills, hoist-limited dumping at a shaft bin. A direct agent-to-agent coordination
model (LHDs "assigned" to trucks) would hard-code dispatch behavior into the physics.

## Decision

The ONLY coupling is material state (`des/materials.py`):

- `OrePassRuntime`: LHD tips add tonnes; the chute draws a truck's payload at load grant. A full
  pass parks the LHD at the tip; an empty pass parks the granted truck UNDER the chute (holding
  its loading spot — physically true). An iterative FIFO `_settle` serves both queues without
  recursion.
- `ShaftBinRuntime`: continuous hoist drain; a full bin holds the dumping truck for the exact
  closed-form time to headroom (never polled).

Conservation is a tested invariant on every run: `tipped == chuted + inventory`, and
`chuted == hauled + in-flight-loading-at-cutoff`.

## Consequences

- Dispatch policies remain truck-side only; LHD behavior is fixed physics (round-robin
  drawpoints). A future LHD-dispatch hook can be added without touching the coupling.
- Starvation/saturation dynamics EMERGE from capacities and cycle times — the phenomena a
  consumer wants to study — rather than being parameterized.
