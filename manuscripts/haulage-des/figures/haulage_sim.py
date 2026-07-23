#!/usr/bin/env python3
"""Reproducible haulage experiments + figure data for the minehaulsim software note, all from the real package.

On ONE generated open-pit mine (fixed geometry, seed 42) we run the deterministic DES and measure:

  (1) the throughput-vs-fleet saturation curve, congested vs free-flow: production rises with the truck fleet and
      then saturates as trucks bunch and queue on the constrained network; the gap to the free-flow (no-traffic)
      run is the EMERGENT congestion the simulator produces without sampling it from a distribution;
  (2) the dispatch-policy comparison at a matched fleet (fixed / nearest / minqueue);
  (3) the road network (node coordinates + segments + loader/dump nodes) for the mine-layout figure.

Determinism: every run is a pure function of (spec, policy, seed). Writes ../data/haulage_sim.json and
../data/network.json.

Run:  python haulage_sim.py
Deps: minehaulsim, numpy.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from minehaulsim import generate_open_pit
from minehaulsim.des.dispatch import FixedPolicy, NearestPolicy, MinQueuePolicy

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
DATA.mkdir(exist_ok=True)

SHIFT_S = 6 * 3600.0
SEED_RUN = 7
POLICIES = {"fixed": FixedPolicy, "nearest": NearestPolicy, "minqueue": MinQueuePolicy}


def _fleet(spec, n):
    """A truck list of size n, cycling the mine's own truck classes and spreading start loaders."""
    units = [t["unit_name"] for t in spec.trucks] or ["CAT_777G"]
    loaders = [ld["node_id"] for ld in spec.loaders]
    trucks = tuple({"truck_id": i + 1, "unit_name": units[i % len(units)],
                    "start_loader": loaders[i % len(loaders)]} for i in range(n))
    return replace(spec, trucks=trucks)


def _tph(spec, n, policy_cls, fast):
    res = _fleet(spec, n).run(policy=policy_cls(), seed=SEED_RUN, until_s=SHIFT_S, fast_mode=fast)
    hours = SHIFT_S / 3600.0
    truck_busy_frac = 1.0 - res.truck_wait_s / max(1.0, n * SHIFT_S)
    return {"n_trucks": n, "tonnes": round(res.tonnes, 1), "tph": round(res.tonnes / hours, 1),
            "cycles": int(res.cycles), "truck_wait_s": round(res.truck_wait_s, 1),
            "truck_busy_frac": round(max(0.0, truck_busy_frac), 3)}


def main():
    spec = generate_open_pit(seed=42)
    n_loaders, n_dumps = len(spec.loaders), len(spec.dumps)
    out = {"meta": {"mine_seed": 42, "shift_hours": SHIFT_S / 3600.0, "run_seed": SEED_RUN,
                    "n_loaders": n_loaders, "n_dumps": n_dumps,
                    "note": "minehaulsim deterministic DES: throughput-vs-fleet (congested vs free-flow), policies"},
           "saturation": [], "policies": []}

    fleet_grid = [1, 2, 3, 4, 6, 8, 10, 12, 15, 18, 22]
    for n in fleet_grid:
        cong = _tph(spec, n, MinQueuePolicy, fast=False)
        free = _tph(spec, n, MinQueuePolicy, fast=True)
        row = {"n_trucks": n, "tph_congested": cong["tph"], "tph_freeflow": free["tph"],
               "busy_frac_congested": cong["truck_busy_frac"], "cycles_congested": cong["cycles"],
               "congestion_loss": round(1.0 - cong["tph"] / max(1.0, free["tph"]), 3)}
        out["saturation"].append(row)
        print(f"n={n:2d}  congested={cong['tph']:.0f} tph  freeflow={free['tph']:.0f} tph  "
              f"loss={row['congestion_loss']:.2f}  busy={cong['truck_busy_frac']:.2f}")

    # policy comparison at a fleet in the saturating region
    n_cmp = 12
    for name, cls in POLICIES.items():
        r = _tph(spec, n_cmp, cls, fast=False)
        out["policies"].append({"policy": name, "n_trucks": n_cmp, "tph": r["tph"],
                                "busy_frac": r["truck_busy_frac"], "cycles": r["cycles"]})
        print(f"policy {name:9s} (n={n_cmp}): {r['tph']:.0f} tph  busy={r['truck_busy_frac']:.2f}")

    (DATA / "haulage_sim.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    # road network for the layout figure: real road polylines (grade-coloured), node kinds, loaders/dumps
    net = spec.network
    node_xyz = {int(nd["id"]): {"pos": [float(c) for c in nd["pos"]], "kind": str(nd.get("kind", ""))}
                for nd in net["nodes"]}
    segs = [{"poly": [[float(p[0]), float(p[1])] for p in s["polyline"]],
             "grade": float(s.get("grade_pct", 0.0)), "one_way": bool(s.get("one_way", False)),
             "single_lane": bool(s.get("single_lane_op", False))} for s in net["segments"]]
    netout = {"nodes": node_xyz, "segments": segs,
              "loaders": [int(l["node_id"]) for l in spec.loaders],
              "dumps": [int(d) for d in spec.dumps]}
    (DATA / "network.json").write_text(json.dumps(netout), encoding="utf-8")
    print(f"wrote haulage_sim.json + network.json ({len(node_xyz)} nodes, {len(segs)} segments)")


if __name__ == "__main__":
    main()
