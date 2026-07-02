# 01 — Layers and data flow

```
scenarios/            geometry/                network/
  openpit_gen ──────►   openpit.build_open_pit ──► RoadNetwork + DirectionZones + Junctions
  underground_gen ───►   underground.build_underground
  spec.MineSpec  ◄──── frozen document: network + rosters + traffic + topo + estimates
  validate       ◄──── 7 named gates (contract, connectivity, grades, geometry, traffic,
       │                throughput, deadlock-free smoke) + batch diversity signatures
       ▼
des/                                             equipment/
  engine (heap, tombstones, deadlock signal)       catalog: rimpull/retarder envelopes
  resources (Queue / Slot / DirectionZone)         network/kinematics: SpeedSolver
  traversal (slots + no-overtake + zones + junctions = traffic)
  sim.run_shift (truck cycle, LHD loop, materials coupling, plan hook, failures hook)
  dispatch (policy protocol + 5 baselines)
       │
       ▼
io/   cyclelog/v1 CSV + consumer-validator port · provenance JSON · PitTopoSpec / minetopo/v1
viz/  (extra) plan view · ramp profile · cycle Gantt        cli/  generate|batch|run|render|validate|demo
```

## The dependency rule

Lower layers never import higher ones: `equipment`/`network` know nothing of the DES; the DES
knows nothing of generators; `io` reads only plain results. `planning/` sits beside the DES and
couples through the small `PlanContext` protocol (`overlay_revision`, `effective_network`,
`is_diggable`, `on_load`) — the simulator never touches pit-model internals.

## Where state lives

Only three mutable things exist at run time: the engine heap, the resource/traffic occupancy,
and (when configured) `PitState` / material inventories. Everything else — networks, specs,
designs, equipment classes — is frozen after construction, which is what makes route caches and
speed tables trustworthy.
