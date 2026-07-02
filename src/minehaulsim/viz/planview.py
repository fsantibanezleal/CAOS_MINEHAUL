"""Plan-view rendering of a generated mine: the picture that PROVES scenario variety.

Draws, for an open-pit MineSpec:
    - bench rings, reconstructed from the spec's sampled rim parameters (the spec stays lean —
      geometry is a deterministic function of `params`, so the view re-derives it)
    - the road network from the serialized polylines: ramps colored by kind (two-way, one-way
      with direction arrows, single-lane zoned), bench arcs, surface trunk
    - sites: faces (numbered), crushers, waste dumps, stockpile, portals, junctions

Everything renders from the spec alone; no live simulation objects needed. SVG output is the
repo-gallery format (text, diffable); PNG works for contact sheets.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ..network.graph import RoadNetwork
from ..scenarios.spec import MineSpec
from . import require_mpl

RING_COLOR = "#9aa5b1"
FLOOR_COLOR = "#e8edf2"
RAMP_COLOR = "#c2410c"
RAMP_ONEWAY_COLOR = "#7c3aed"
RAMP_ZONED_COLOR = "#b91c1c"
BENCH_ARC_COLOR = "#0369a1"
SURFACE_COLOR = "#4d7c0f"


def _rim_shape_from_params(params: dict):
    from ..geometry.openpit import RimShape
    return RimShape(
        rx=float(params["rim_rx_m"]), ry=float(params["rim_ry_m"]),
        n_exp=float(params["superellipse_n"]), azimuth_rad=float(params["rim_azimuth_rad"]),
        harmonics=tuple((int(k), float(a), float(p)) for k, a, p in params.get("harmonics", [])),
        sector_boosts=tuple((float(c), float(w), float(b))
                            for c, w, b in params.get("sector_boosts", [])),
    )


def _rings_xy(params: dict, n_pts: int = 256) -> list[np.ndarray]:
    shape = _rim_shape_from_params(params)
    step_in = float(params["step_in_m"])
    th = np.linspace(0.0, 2 * math.pi, n_pts)
    out = []
    for i in range(int(params["n_benches"]) + 1):
        r = np.maximum(shape.radius(th) - i * step_in, 0.0)
        out.append(np.stack([r * np.cos(th), r * np.sin(th)], axis=1))
    return out


def plot_plan(spec: MineSpec, ax=None):
    """Draw the plan view onto `ax` (created when None). Returns the matplotlib Axes."""
    require_mpl()
    import matplotlib.pyplot as plt

    if ax is None:
        _, ax = plt.subplots(figsize=(7.2, 7.2))
    net = RoadNetwork.from_dict(spec.network)

    if spec.kind == "openpit":
        rings = _rings_xy(spec.params)
        ax.fill(rings[-1][:, 0], rings[-1][:, 1], color=FLOOR_COLOR, zorder=0)
        for ring in rings:
            ax.plot(ring[:, 0], ring[:, 1], color=RING_COLOR, lw=0.6, zorder=1)

    for seg in net.segments.values():
        xy = seg.polyline[:, :2]
        if abs(seg.grade_pct) > 1e-9:                       # ramp
            color = RAMP_ONEWAY_COLOR if seg.one_way else (
                RAMP_ZONED_COLOR if seg.zone_id is not None else RAMP_COLOR)
            ax.plot(xy[:, 0], xy[:, 1], color=color, lw=2.0, zorder=3,
                    linestyle=(0, (4, 1.5)) if seg.zone_id is not None else "-")
            if seg.one_way and len(xy) >= 2:
                mid = len(xy) // 2
                d = xy[min(mid + 1, len(xy) - 1)] - xy[mid]
                n = float(np.hypot(*d))
                if n > 0:
                    ax.annotate("", xytext=xy[mid], xy=xy[mid] + d / n * 30.0, zorder=4,
                                arrowprops=dict(arrowstyle="-|>", color=color, lw=1.4))
        elif net.nodes[seg.a].pos[2] < -1e-9 or net.nodes[seg.b].pos[2] < -1e-9:
            ax.plot(xy[:, 0], xy[:, 1], color=BENCH_ARC_COLOR, lw=1.4, zorder=2)
        else:
            ax.plot(xy[:, 0], xy[:, 1], color=SURFACE_COLOR, lw=1.6, zorder=2)

    loaders = {int(x["node_id"]) for x in spec.loaders}
    for n in net.nodes.values():
        x, y = n.pos[0], n.pos[1]
        if n.id in loaders:
            ax.scatter([x], [y], marker="^", s=90, color="#111827", zorder=5)
            ax.annotate(str(n.id), (x, y), textcoords="offset points", xytext=(6, 6),
                        fontsize=8, fontweight="bold", zorder=6)
        elif n.kind == "crusher":
            ax.scatter([x], [y], marker="s", s=80, color="#1d4ed8", zorder=5)
        elif n.kind == "dump":
            ax.scatter([x], [y], marker="X", s=80, color="#a16207", zorder=5)
        elif n.kind == "portal":
            ax.scatter([x], [y], marker="D", s=50, color="#7c3aed", zorder=5)
        elif n.kind == "junction":
            ax.scatter([x], [y], marker="o", s=28, color="#4d7c0f", zorder=5)

    p = spec.params
    ax.set_title(
        f"{spec.name} — {p.get('ramp_style', '?')}, {p.get('n_benches', '?')} benches, "
        f"{len(spec.loaders)} shovels, {len(spec.dumps)} dumps, seed {spec.seed}",
        fontsize=10)
    ax.set_aspect("equal")
    ax.set_xlabel("east [m]", fontsize=8)
    ax.set_ylabel("north [m]", fontsize=8)
    ax.tick_params(labelsize=7)
    return ax


def save_planview(spec: MineSpec, path: str | Path) -> Path:
    """Render the plan view to `path` (format from the suffix: .svg for the gallery, .png ok)."""
    require_mpl()
    import matplotlib.pyplot as plt

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ax = plot_plan(spec)
    fig = ax.figure
    fig.tight_layout()
    fig.savefig(p, format=p.suffix.lstrip("."), dpi=110)
    plt.close(fig)
    return p
