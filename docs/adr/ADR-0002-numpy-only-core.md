# ADR-0002 — numpy-only core; matplotlib as an opt-in extra

- Status: Accepted · Date: 2026-07-02 (recorded at U12; decision made at U1)

## Context

The package is consumed as a library by data pipelines (DispatchLab's generator venv) where
every transitive dependency is friction, and its outputs must be reproducible byte-for-byte.

## Decision

Runtime dependency = `numpy` only. Geometry (polylines), curve envelopes and the RNG all use
numpy primitives. Visualization lives in `minehaulsim.viz`, import-guarded behind the
`[viz]` extra, and forces the Agg backend (the viz API is a FILE renderer by design — SVG
gallery artifacts and PNG contact sheets, never interactive windows).

## Consequences

- `pip install minehaulsim` is light and safe in CI/pipeline venvs.
- Core modules must never import matplotlib (a guard raises a clear error naming the extra).
- Rendering differences across matplotlib versions cannot affect simulation outputs — they are
  strictly downstream of the deterministic artifacts.
