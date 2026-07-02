"""Ramp grade profile + single-truck cycle Gantt — the two diagnostic side views.

`save_ramp_profile`: elevation vs cumulative horizontal distance for every ramp chain (connected
component of graded segments), annotated with the design grade. Dual-spiral pits show two chains.

`save_cycle_gantt`: one truck's shift as phase bars reconstructed from cyclelog events (the same
event anchoring the IO contract documents: load->haul = loading, haul->dump = loaded travel,
dump->return = dumping, return->next load = empty travel + queue).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from ..network.graph import RoadNetwork
from ..scenarios.spec import MineSpec
from . import require_mpl

PHASE_COLORS = {"loading": "#0369a1", "loaded travel": "#c2410c",
                "dumping": "#4d7c0f", "empty travel + queue": "#9aa5b1"}


def _ramp_components(net: RoadNetwork) -> list[list[int]]:
    """Connected components over graded segments (shared endpoints), each ordered by descending
    elevation — one component per physical ramp."""
    ramp = {s.id: s for s in net.segments.values() if abs(s.grade_pct) > 1e-9}
    parent = {sid: sid for sid in ramp}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_node: dict[int, list[int]] = {}
    for s in ramp.values():
        by_node.setdefault(s.a, []).append(s.id)
        by_node.setdefault(s.b, []).append(s.id)
    for sids in by_node.values():
        for other in sids[1:]:
            ra, rb = find(sids[0]), find(other)
            if ra != rb:
                parent[rb] = ra
    groups: dict[int, list[int]] = {}
    for sid in ramp:
        groups.setdefault(find(sid), []).append(sid)
    out = []
    for sids in groups.values():
        out.append(sorted(sids, key=lambda i: -float(np.max(ramp[i].polyline[:, 2]))))
    out.sort(key=lambda sids: sids[0])
    return out


def save_ramp_profile(spec: MineSpec, path: str | Path) -> Path:
    require_mpl()
    import matplotlib.pyplot as plt

    net = RoadNetwork.from_dict(spec.network)
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    for ci, sids in enumerate(_ramp_components(net)):
        run = 0.0
        xs: list[float] = []
        zs: list[float] = []
        for sid in sids:
            poly = net.segments[sid].polyline
            # orient each span top -> bottom so the profile always descends
            p = poly if poly[0, 2] >= poly[-1, 2] else poly[::-1]
            d = np.hypot(np.diff(p[:, 0]), np.diff(p[:, 1]))
            xs.extend((run + np.concatenate([[0.0], np.cumsum(d)])).tolist())
            zs.extend(p[:, 2].tolist())
            run += float(np.sum(d))
        ax.plot(xs, zs, lw=1.8, label=f"ramp {ci + 1}")
    grade = spec.params.get("ramp_grade_pct")
    style = spec.params.get("ramp_style", "?")
    ax.set_title(f"{spec.name} — ramp profile ({style}, {grade}% design grade)", fontsize=10)
    ax.set_xlabel("cumulative horizontal distance [m]", fontsize=8)
    ax.set_ylabel("elevation [m]", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(p, format=p.suffix.lstrip("."), dpi=110)
    plt.close(fig)
    return p


def save_cycle_gantt(events: list[dict], truck_id: int, path: str | Path,
                     title: str = "") -> Path:
    require_mpl()
    import matplotlib.pyplot as plt

    evs = sorted((e for e in events if e["truck_id"] == truck_id), key=lambda e: e["t"])
    if not evs:
        raise ValueError(f"no events for truck {truck_id}")
    spans: dict[str, list[tuple[float, float]]] = {k: [] for k in PHASE_COLORS}
    phase_of = {"load": "loading", "haul": "loaded travel", "dump": "dumping",
                "return": "empty travel + queue"}
    for a, b in zip(evs, evs[1:]):
        spans[phase_of[a["event"]]].append((a["t"] / 60.0, (b["t"] - a["t"]) / 60.0))

    fig, ax = plt.subplots(figsize=(8.0, 2.6))
    for row, (label, bars) in enumerate(spans.items()):
        ax.broken_barh(bars, (row - 0.35, 0.7), facecolors=PHASE_COLORS[label])
    ax.set_yticks(range(len(spans)), labels=list(spans), fontsize=8)
    ax.set_xlabel("shift time [min]", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.set_title(title or f"truck {truck_id} cycle phases", fontsize=10)
    ax.grid(axis="x", alpha=0.3)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(p, format=p.suffix.lstrip("."), dpi=110)
    plt.close(fig)
    return p
