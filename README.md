# minehaulsim

**Deterministic discrete-event simulation of open-pit and underground mine haulage on constrained
road networks, with seeded parametric mine generators.**

No open-source package simulates mine haulage on a *real constrained road network*: existing OSS
simulators use one fixed mine layout, scalar distance matrices, no grades/rimpull and no traffic
constraints — and the tools that do this right (HAULSIM, TALPAC-3D, SimMine) are commercial and
closed. `minehaulsim` fills that gap:

- **Constrained road network, first-class.** Haul routes are a directed multigraph: one-way ramps,
  width/passing classes, junction blocking, direction zones (single-lane drifts with passing bays),
  speed-by-grade from rimpull/retarder curves — travel times come from the network, not scalars.
- **Genuinely varied mines.** Seeded parametric generators for open pits (benches, phases,
  spiral/switchback ramps, multiple faces/dumps) and underground multi-level mines (levels,
  declines, shafts, ore passes, drifts) — a different, valid mine per seed, never one shape reused.
- **Deterministic DES core.** Hand-rolled event engine (no simpy); a run is a pure function of
  `(spec, policy, seed)` — byte-identical outputs across OS/sessions. Dispatch-policy hook with
  baseline policies included.
- **Interoperable outputs.** `cyclelog/v1` CSV event logs (load/haul/dump/return), provenance JSON,
  a per-truck position trace, and topography exports for 3D viewers.
- **numpy-only core.** Visualization is an opt-in extra; the core never imports matplotlib.

## Status

Early alpha (`0.01.000`): scaffold + the determinism foundation (typed core + seeded stream
manager). The layers land in verifiable units — see `CHANGELOG.md`.

## Install (dev)

```bash
pip install -e ".[dev]"
pytest
```

## Honesty

This is a *simulation* package: equipment curves are class-representative (not OEM data), generated
mines are synthetic (structure-real at best, and always labelled), and nothing here predicts a real
operation without calibration. The docs carry a dedicated *what-it-is-and-isn't* section.

## License

Apache-2.0.
