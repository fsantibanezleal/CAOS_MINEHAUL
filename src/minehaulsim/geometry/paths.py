"""Generic polyline helpers shared by the geometry builders (open pit U8, underground U10).

Polylines are (k >= 2, 3) float64 arrays in world metres, matching network.graph.Segment.
"""
from __future__ import annotations

import numpy as np


def polyline_length(pts: np.ndarray) -> float:
    """3D arc length of a polyline."""
    d = np.diff(np.asarray(pts, dtype=np.float64), axis=0)
    return float(np.sum(np.sqrt(np.sum(d * d, axis=1))))


def horizontal_length(pts: np.ndarray) -> float:
    """Plan-view (xy) arc length — the denominator of a grade."""
    d = np.diff(np.asarray(pts, dtype=np.float64)[:, :2], axis=0)
    return float(np.sum(np.sqrt(np.sum(d * d, axis=1))))


def signed_grade_pct(pts: np.ndarray) -> float:
    """End-to-end signed grade [%] of a polyline: rise over horizontal run, first -> last point."""
    pts = np.asarray(pts, dtype=np.float64)
    run = horizontal_length(pts)
    if run <= 0:
        raise ValueError("grade undefined: polyline has no horizontal extent")
    return 100.0 * float(pts[-1, 2] - pts[0, 2]) / run


def straight(a: tuple[float, float, float], b: tuple[float, float, float]) -> np.ndarray:
    """Two-point straight polyline a -> b."""
    return np.array([a, b], dtype=np.float64)
