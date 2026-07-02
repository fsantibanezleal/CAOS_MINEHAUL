# Changelog

Display versions `X.XX.XXX` (PEP 440 normalized in pyproject). Tag every release `vX.XX.XXX`.

## [0.01.000] — 2026-07-02

### Added
- U1 scaffold: src-layout package (`minehaulsim` + `minehaulsim_cli`), Apache-2.0, CI (ruff + pytest
  on py3.11–3.13, ubuntu + windows), VERSION/CHANGELOG discipline.
- `types.py`: unit conventions (m, s, t, signed grade fractions), core ids/enums (`SiteKind`,
  `CycleEvent` = the cyclelog/v1 tokens, `MineKind`), `XYZ`.
- `rng.py`: `RngManager` — named independent child streams from one master seed
  (SeedSequence-derived; byte-stable across OS/sessions; the package's only randomness source).
