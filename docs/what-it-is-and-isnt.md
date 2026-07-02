# What minehaulsim is — and is NOT

Honesty page. Read this before trusting any number the package produces.

## What it IS

- A **deterministic discrete-event simulator** of mine haulage on a **constrained road network**:
  one-way ramps, width classes, single-lane direction zones with arbitration policies, junction
  blocking, headway-based segment capacity and a FIFO no-overtake rule that makes **bunching
  emerge** instead of being sampled from a distribution.
- A **speed-by-grade model** in the TALPAC/FPC tradition: attainable speed solved from
  rimpull/retarder force envelopes against grade + rolling resistance, per truck class and GVW.
- A **seeded parametric scenario generator** for open pits (perturbed-superellipse rims, three
  ramp topologies, phases) and multi-level underground mines (declines with passing bays, drift
  zones, ore passes, shaft bins), each gated by named validity checks — a structurally different,
  valid mine per seed.
- A **mine-planning layer**: phases/pushbacks, legal depletion with exact conservation, network
  evolution as benches complete, slope-damage closures, speed zones — coupled to the simulator so
  cyclelog tonnes and model depletion are the same number by construction.
- An **interoperability layer**: cyclelog/v1 CSV (the DispatchLab ingest contract, including a
  faithful port of the consumer's validator), provenance JSON, PitTopoSpec / minetopo/v1
  topography exports.

## What it is NOT

- **Not OEM performance data.** Equipment curves are class-representative envelopes generated
  from first principles (`F(v) = min(F_traction, eta*P/v)`) around public spec-sheet magnitudes.
  Cycle times and grade sensitivity behave like the machine class; they are NOT a manufacturer's
  FPC table and must not be used for procurement or contractual productivity claims.
- **Not a geology or blending model.** Material is a single homogeneous "tonnes" stream; there
  are no grades (ore quality), no blending targets, no dilution, no stockpile rehandle economics.
- **Not a fuel/emissions model** (v0.x): no fuel burn, tyre wear, or carbon accounting.
- **Not calibrated to any real operation.** Generated mines are synthetic —
  *structure-real at best, always labelled* in the provenance. Nothing here predicts a specific
  mine without calibration against its data.
- **Not a real-time dispatch product.** The dispatch policies are transparent baselines for
  research comparison, not a commercial FMS. The OR/assignment tier lives in consumers
  (e.g. DispatchLab), not here.
- **v1 simplifications, stated:** truck breakdowns materialize at cycle boundaries (no
  mid-segment blocking); underground truck_direct abstracts the LHD shuttle into the loading
  class; surface roads are flat; the shaft hoist is a continuous drain, not a skip cycle.
