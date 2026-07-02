# Changelog

Display versions `X.XX.XXX` (PEP 440 normalized in pyproject). Tag every release `vX.XX.XXX`.

## [0.05.000] — 2026-07-02

### Added
- U11 failures (`des/failures.py`, OFF by default): per-truck exponential-TBF breakdowns with
  lognormal repairs (materialize at the cycle boundary — the truck parks at its node),
  per-loader downtime (the granted truck waits through the repair; dispatch sees it via
  est_free_s), and scheduled segment-closure windows fed into the router's `closed` set. A
  loaded truck caught at the face by a closure HOLDS its spot and retries until the window
  reopens. `ShiftResult.downtime` carries per-unit repair seconds.
- U11 perf: `scripts/bench_engine.py` + `tests/test_perf.py` CI floor (>= 20k executed
  events/s on the reference preset, traffic ON). Measured locally: ~77k ev/s (starter pit),
  ~63k ev/s (48-truck pit) — comfortably above the floor; no `__slots__` pass needed.

## [0.04.000] — 2026-07-02

### Added
- U10 underground: `geometry/underground.py` (multi-level solids — spiral/zigzag declines with
  passing bays splitting every span into a DirectionZone, capacity-1 drift zones with drawpoint
  fans, ore-pass tips + haulage chutes, optional shaft bin, zigzag turns as capacity-1
  junctions), `des/materials.py` (`OrePassRuntime` with exact conservation, `ShaftBinRuntime`
  with closed-form hoist-drain waits), LHD agents in the DES (dig -> tram -> tip -> return;
  a full pass parks the LHD, an empty pass parks the loading truck under the chute — the two
  fleets couple ONLY through inventory), `scenarios/underground_gen.py` (all axes sampled,
  three flow modes: lhd_orepass_truck / truck_direct / truck_shaft; fleet sized to target MF),
  MineSpec carries `lhds` + `materials` (additive, schema unchanged) and a `minetopo/v1` topo
  payload; UG loading classes (CHUTE, LHD_x_LOADING) in the catalog.
- ShiftResult.materials: per-pass conservation summaries (+ the in-flight loading term that
  closes the balance at cutoff) and the shaft-bin hoist summary.

## [0.03.000] — 2026-07-02

### Added
- U9 viz extra (`minehaulsim[viz]`, headless Agg by design): `viz/planview.py` (plan view from
  the spec alone — rings re-derived from sampled rim params, ramps colored by kind with one-way
  arrows, zoned ramps dashed, numbered faces / crushers / dumps / portals / junctions),
  `viz/profile.py` (ramp grade profile per connected ramp chain + single-truck cycle Gantt).
- U9 CLI (blueprint 4.5): `generate` (seed or preset, spec JSON + plan SVG), `batch`, `run`
  (cyclelog/v1 + provenance + topo export, consumer-contract gated, `--fast` free-flow),
  `render`, `validate` (.csv consumer rules / .json the 7 named gates), `demo`, `info`.
- `scripts/gen_gallery.py`: the committed 12-seed gallery (SVG per pit + README + PNG contact
  sheet) proving structural variety; `scripts/demo_offline.py` end-to-end.

## [0.02.000] — 2026-07-02

Consolidates build units U2..U8 (each merged via its own PR; see the git history for the
per-unit record).

### Added
- U2 equipment: truck/loader/LHD catalog with first-principles rimpull + retarder envelopes;
  TALPAC/FPC speed solver (`attainable_speed_kmh`, memoized `SpeedSolver`, accel-penalty
  `traverse_time_s`).
- U3 network: constrained directed multigraph (`RoadNetwork`, `Segment` with signed grade,
  width class, one-way, `DirectionZone` membership), `DirectionZone` (lockout / loaded_priority /
  group_batching), `Junction`, headway-based `segment_capacity`.
- U4 routing: constrained shortest-expected-time `Router` (width/one-way/closures/speed-cap
  admissibility, junction cross cost, deterministic ties, state-keyed cache).
- U5 DES: deterministic event engine (heap + tombstones, `SimulationDeadlock`),
  `QueueResource` / `SlotResource` / `DirectionZoneResource` (the three arbitration policies).
- U6 haul cycle: `run_shift` (full load-haul-dump-return cycle, plan coupling via `PlanContext`,
  loader migration with the advancing front), five baseline dispatch policies, per-segment
  traffic traversal (slot capacity + FIFO no-overtake headway -> emergent bunching; junction and
  direction-zone holds); `fast_mode` free-flow bypass.
- Planning layer (U-P1..P4): `PitModel`/`Bench`/`DigBlock`, `MinePlan` with precedence
  validation, mutable `PitState` (legality gate, exact depletion conservation, bench-completion
  cascade with legacy spurs, snapshots), `NetworkOverlay` (two-tier revision protocol),
  slope-damage resolution (severity ladder -> derates/closures), speed zones (MIN composition),
  evaluation APIs (`pit_summary`, `reachability`, `plan_feasibility`).
- U7 IO: cyclelog/v1 writer + faithful consumer-side validator, provenance JSON, PitTopoSpec
  export with least-squares rim-ellipse fit, minetopo writer.
- U8 scenarios: parametric open-pit geometry (`geometry/openpit.py` — perturbed-superellipse rim
  with sector-boosted phases, bench rings by step-in, spiral / switchback / dual_spiral ramps
  split into constant-grade segments, faces arc-tied to every ramp, ex-pit destinations behind a
  shared junction trunk), `MineSpec` frozen scenario document (canonical JSON, `to_runtime()`,
  `run()`), `generate_open_pit` / `generate_batch` sampling every structural axis with fleet
  sizing to a target match factor, seven named validity gates + batch diversity signatures,
  four presets. `Segment.single_lane_op` marks wide-vehicle single-lane ramps so they can join
  DirectionZones.

## [0.01.000] — 2026-07-02

### Added
- U1 scaffold: src-layout package (`minehaulsim` + `minehaulsim_cli`), Apache-2.0, CI (ruff + pytest
  on py3.11–3.13, ubuntu + windows), VERSION/CHANGELOG discipline.
- `types.py`: unit conventions (m, s, t, signed grade fractions), core ids/enums (`SiteKind`,
  `CycleEvent` = the cyclelog/v1 tokens, `MineKind`), `XYZ`.
- `rng.py`: `RngManager` — named independent child streams from one master seed
  (SeedSequence-derived; byte-stable across OS/sessions; the package's only randomness source).
