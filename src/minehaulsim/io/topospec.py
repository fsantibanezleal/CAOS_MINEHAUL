"""Topography exports: the EXACT DispatchLab PitTopoSpec JSON (so its 3D view renders the REAL
generated geometry) + our minehaulsim.minetopo/v1 for underground (a future 3D contract)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


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


def write_pit_topo_spec(path: str | Path, *, center: tuple[float, float], rim_xy: np.ndarray,
                        n_benches: int, bench_height_m: float, bench_width_m: float,
                        face_angle_deg: float, ramp_width_m: float,
                        shovel_bench: dict[int, int]) -> dict:
    rx, ry = fit_ellipse_axes(rim_xy, center)
    spec = {
        "center": {"x": center[0], "y": center[1]},
        "rimRx": round(rx, 2), "rimRy": round(ry, 2),
        "nBenches": n_benches, "benchHeightM": bench_height_m, "benchWidthM": bench_width_m,
        "faceAngleDeg": face_angle_deg, "rampWidthM": ramp_width_m,
        "shovelBench": {str(k): int(v) for k, v in sorted(shovel_bench.items())},
    }
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
