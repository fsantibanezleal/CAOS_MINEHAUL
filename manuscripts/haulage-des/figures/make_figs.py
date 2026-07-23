#!/usr/bin/env python3
"""Regenerate the data figures for the minehaulsim software note from the COMMITTED artifacts (produced by
haulage_sim.py running the real deterministic DES). Two figures:

  fig-network.pdf     - a plan view of the generated open-pit haul road network: the real road polylines
                        (coloured by grade), with the loader (shovel) and dump nodes marked.
  fig-throughput.pdf  - (a) the throughput-vs-fleet saturation curve, congested vs free-flow (the gap is the
                        emergent congestion) with the truck-busy fraction; (b) the dispatch-policy comparison.

The hand-authored fig-cycle.svg (the DES haul-cycle schematic) is converted to PDF separately via svglib.

Run:  python make_figs.py
Deps: matplotlib, numpy.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"

INK = "#1a1a2e"
GRID = "#d8d8e0"
CONG = "#1b6ca8"
FREE = "#e07a3f"

plt.rcParams.update({
    "font.family": "serif", "font.size": 9.4, "axes.edgecolor": INK,
    "axes.labelcolor": INK, "text.color": INK, "xtick.color": INK, "ytick.color": INK,
    "axes.linewidth": 0.8, "figure.dpi": 200,
})


def fig_network():
    net = json.loads((DATA / "network.json").read_text(encoding="utf-8"))
    nodes = {int(k): v for k, v in net["nodes"].items()}
    fig, ax = plt.subplots(figsize=(4.6, 4.0))
    segs, grades = [], []
    for s in net["segments"]:
        p = np.asarray(s["poly"])
        segs.append(p)
        grades.append(s["grade"])
    lc = LineCollection(segs, array=np.abs(np.asarray(grades)), cmap="viridis", linewidths=2.2)
    ax.add_collection(lc)
    cb = fig.colorbar(lc, ax=ax, fraction=0.046, pad=0.02)
    cb.set_label("|grade| (%)", fontsize=8.4)
    # loaders + dumps + portal
    for nid in net["loaders"]:
        x, y, *_ = nodes[nid]["pos"]
        ax.plot(x, y, "s", color="#b23a48", markersize=9, markeredgecolor="k", markeredgewidth=0.6, zorder=5)
    for nid in net["dumps"]:
        x, y, *_ = nodes[nid]["pos"]
        ax.plot(x, y, "v", color="#2f7d3a", markersize=8, markeredgecolor="k", markeredgewidth=0.6, zorder=5)
    for nid, nd in nodes.items():
        if nd.get("kind") == "portal":
            x, y, *_ = nd["pos"]
            ax.plot(x, y, "*", color="#e07a3f", markersize=12, markeredgecolor="k", markeredgewidth=0.5, zorder=6)
    ax.plot([], [], "s", color="#b23a48", label="loader (shovel)")
    ax.plot([], [], "v", color="#2f7d3a", label="dump")
    ax.plot([], [], "*", color="#e07a3f", label="portal")
    ax.set_aspect("equal")
    ax.autoscale()
    ax.set_xlabel("easting (m)"); ax.set_ylabel("northing (m)")
    ax.set_title("generated open-pit haul network", fontsize=9.4)
    ax.legend(fontsize=7.6, frameon=True, facecolor="white", edgecolor=GRID, loc="upper right")
    ax.grid(True, color=GRID, linewidth=0.5)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(HERE / "fig-network.pdf", bbox_inches="tight")
    plt.close(fig)


def fig_throughput():
    d = json.loads((DATA / "haulage_sim.json").read_text(encoding="utf-8"))
    sat = d["saturation"]
    n = [r["n_trucks"] for r in sat]
    cong = [r["tph_congested"] for r in sat]
    free = [r["tph_freeflow"] for r in sat]
    busy = [r["busy_frac_congested"] for r in sat]
    fig, (axa, axb) = plt.subplots(1, 2, figsize=(7.0, 3.1), gridspec_kw={"width_ratios": [1.35, 1]})

    axa.plot(n, free, "o--", color=FREE, linewidth=1.5, markersize=4.5, label="free-flow (no traffic)")
    axa.plot(n, cong, "o-", color=CONG, linewidth=1.8, markersize=5, label="congested (constrained network)")
    axa.fill_between(n, cong, free, color=CONG, alpha=0.10)
    axa.set_xlabel("truck fleet size")
    axa.set_ylabel("production (tonnes / h)")
    axa.set_title("(a) throughput saturates; the gap is\nemergent congestion", fontsize=8.8)
    axa.grid(True, color=GRID, linewidth=0.7)
    axa.set_axisbelow(True)
    axa.legend(fontsize=7.4, frameon=True, facecolor="white", edgecolor=GRID, loc="lower right")
    for s in ("top", "right"):
        axa.spines[s].set_visible(False)
    # truck busy fraction on a twin axis
    ax2 = axa.twinx()
    ax2.plot(n, busy, "^:", color="#7d5ba6", linewidth=1.2, markersize=4, label="truck busy fraction")
    ax2.set_ylabel("truck busy fraction", color="#7d5ba6", fontsize=8.6)
    ax2.tick_params(axis="y", labelcolor="#7d5ba6")
    ax2.set_ylim(0.5, 1.02)
    ax2.spines["top"].set_visible(False)

    pol = d["policies"]
    names = [p["policy"] for p in pol]
    tph = [p["tph"] for p in pol]
    cols = {"minqueue": "#1b6ca8", "fixed": "#3fa34d", "nearest": "#b23a48"}
    bars = axb.bar(names, tph, color=[cols.get(x, "#888") for x in names], edgecolor=INK, linewidth=0.6, width=0.62)
    for b, p in zip(bars, pol):
        axb.text(b.get_x() + b.get_width() / 2, p["tph"] + 40, f"{p['tph']:.0f}\nbusy {p['busy_frac']:.2f}",
                 ha="center", va="bottom", fontsize=7.4)
    axb.set_ylabel("production (tonnes / h)")
    axb.set_ylim(0, max(tph) * 1.22)
    axb.set_title(f"(b) dispatch policy\n(fleet = {pol[0]['n_trucks']})", fontsize=8.8)
    axb.grid(axis="y", color=GRID, linewidth=0.7)
    axb.set_axisbelow(True)
    for s in ("top", "right"):
        axb.spines[s].set_visible(False)

    fig.tight_layout()
    fig.savefig(HERE / "fig-throughput.pdf", bbox_inches="tight")
    plt.close(fig)


def main():
    fig_network()
    fig_throughput()
    print("wrote fig-network.pdf, fig-throughput.pdf")


if __name__ == "__main__":
    main()
