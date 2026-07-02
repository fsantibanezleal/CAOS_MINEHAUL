# Architecture

How the package is layered and why a run is reproducible byte-for-byte.

- [01 — Layers and data flow](01_layers.md)
- [02 — The engine and determinism](02_engine-and-determinism.md)
- [03 — Network, geometry and the planning overlay](03_network-geometry-planning.md)

One paragraph version: **scenario documents in, artifacts out, everything in between a pure
function of `(spec, policy, seed)`.** Generators sample a frozen `MineSpec` from named RNG
streams and gate it through validity checks; `run_shift` executes the haul cycle on a frozen
road network with traffic resources arbitrating every conflict; the IO layer writes the
consumer contracts with rounding applied only at the boundary.
