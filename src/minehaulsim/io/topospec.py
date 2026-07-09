"""Topography exports: the EXACT DispatchLab PitTopoSpec JSON (so its 3D view renders the REAL
generated geometry) + our minehaulsim.minetopo/v1 for underground (a future 3D contract)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ..network.graph import RoadNetwork


def fit_ellipse_axes(rim_xy: np.ndarray, center: tuple[float, float]) -> tuple[float, float]:
    """Least-squares axis fit of a (possibly perturbed) rim around a known center:
    minimize sum((x/rx)^2 + (y/ry)^2 - 1)^2 -> closed form via least squares on (x^2, y^2)."""
    x = rim_xy[:, 0] - center[0]
    y = rim_xy[:, 1] - center[1]
    A = np.stack([x * x, y * y], axis=1)
    b = np.ones(len(rim_xy))
    coef, *_ = np.linalg.lstsq(A, b, rcond=None)
    coef = np.maximum(coef, 1e-12)
    return float(1.0 / np.sqrt(coef[0])), float(1.0 / np.sqrt(coef[1]))


def road_network_block(network: "RoadNetwork", *, headway_m: float | None = None,
                       headway_s: float | None = None) -> dict:
    """The `minehaulsim.roads/v1` sidecar: the REAL constrained road network so a 3D consumer
    (DispatchLab) renders the actual generated roads (portal, junctions, surface trunk + spurs,
    in-pit ramps) with their polylines, one-way flags, speed limits and direction zones, plus the
    traffic parameters (headway) that make the car-following / no-overtake bunching reproducible.
    Everything a consumer needs to draw the roads AND mirror the segment traffic model, not
    re-derive an approximation."""
    return {
        "schema": "minehaulsim.roads/v1",
        "nodes": [{"id": n.id, "kind": n.kind, "pos": [round(float(v), 2) for v in n.pos]}
                  for n in sorted(network.nodes.values(), key=lambda n: n.id)],
        "segments": [{
            "id": s.id, "a": s.a, "b": s.b,
            "polyline": [[round(float(v), 2) for v in pt] for pt in s.polyline.tolist()],
            "oneWay": bool(s.one_way), "speedLimitKmh": round(float(s.speed_limit_kmh), 1),
            "rollingResistancePct": round(float(getattr(s, "rolling_resistance_pct", 0.0)), 2),
            "zoneId": s.zone_id,
        } for s in sorted(network.segments.values(), key=lambda s: s.id)],
        "traffic": {k: round(float(v), 3) for k, v in (("headwayM", headway_m), ("headwayS", headway_s))
                    if v is not None},
    }


def write_pit_topo_spec(path: str | Path, *, center: tuple[float, float], rim_xy: np.ndarray,
                        n_benches: int, bench_height_m: float, bench_width_m: float,
                        face_angle_deg: float, ramp_width_m: float,
                        shovel_bench: dict[int, int],
                        network: "RoadNetwork | None" = None,
                        headway_m: float | None = None, headway_s: float | None = None) -> dict:
    rx, ry = fit_ellipse_axes(rim_xy, center)
    spec = {
        "center": {"x": center[0], "y": center[1]},
        "rimRx": round(rx, 2), "rimRy": round(ry, 2),
        "nBenches": n_benches, "benchHeightM": bench_height_m, "benchWidthM": bench_width_m,
        "faceAngleDeg": face_angle_deg, "rampWidthM": ramp_width_m,
        "shovelBench": {str(k): int(v) for k, v in sorted(shovel_bench.items())},
    }
    # optional: carry the REAL road network so the 3D view renders the actual roads (#28), never a
    # re-derived straight-line approximation. Backward compatible: omitted when no network is passed.
    if network is not None:
        spec["roads"] = road_network_block(network, headway_m=headway_m, headway_s=headway_s)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
    return spec


def write_mine_topo(path: str | Path, *, levels: list[dict], decline: list[list[float]],
                    shafts: list[dict], ore_passes: list[dict]) -> dict:
    doc = {"schema": "minehaulsim.minetopo/v1", "coords": "metres",
           "levels": levels, "decline": decline, "shafts": shafts, "ore_passes": ore_passes}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    return doc
