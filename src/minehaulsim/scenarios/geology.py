"""Geology attachment — ground a generated scenario in a MineLib-nature block model (oreblocks).

`attach_geology(spec)` builds a seeded synthetic deposit (grades on a bench-aligned 3-D grid),
solves the EXACT ultimate pit (max-closure), and stamps every loader with the geology of ITS OWN
bench (the bench each shovel already sits on per `topo.shovelBench`): mean in-pit grade at that
level, ore fraction at the economic cutoff, and the tonnage exposed there. The scenario document
gains a `materials["geology"]` block (archetype, seed, econ, the stamped exact pit value) so the
provenance is auditable and deterministic.

Honest scope (v1): the block-model grid is bench-aligned in the VERTICAL axis (n benches, bench
height) but does not reproduce the superellipse footprint horizontally — grades per bench are
statistics of the exact pit at that level, not a voxel-per-voxel match of the haulage topography.
That is exactly what dispatch consumers need (grade/ore-fraction at each loading face) without
pretending a geometric identity the generator does not have.

Requires the `geology` extra: ``pip install minehaulsim[geology]`` (pure-numpy `oreblocks`).
"""
from __future__ import annotations

from dataclasses import replace

from .spec import MineSpec

__all__ = ["attach_geology", "GEOLOGY_SCHEMA"]

GEOLOGY_SCHEMA = "minehaulsim.geology/v1"


def _oreblocks():
    try:
        import oreblocks
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "attach_geology needs the 'geology' extra: pip install minehaulsim[geology]"
        ) from e
    return oreblocks


def attach_geology(
    spec: MineSpec,
    archetype: str = "porphyry",
    *,
    seed: int | None = None,
    peak_grade: float = 0.02,
    price: float = 9000.0,
    recovery: float = 0.88,
    mining_cost: float = 2.5,
    processing_cost: float = 9.0,
    slope_deg: float = 45.0,
) -> MineSpec:
    """Return a copy of ``spec`` with per-loader face geology + a materials.geology block.

    Deterministic in (spec, archetype, seed): the default seed is the spec's own generator seed.
    Open-pit specs only (underground geology is a different contract).
    """
    if spec.kind != "openpit":
        raise ValueError(f"attach_geology supports openpit specs, got kind={spec.kind!r}")
    ob = _oreblocks()

    nb = int(spec.topo.get("nBenches") or spec.params.get("n_benches") or 12)
    bench_h = float(spec.topo.get("benchHeightM") or spec.params.get("bench_height_m") or 12.0)
    gseed = spec.seed if seed is None else int(seed)

    # bench-aligned grid: nz = the pit's bench count; the footprint is a generic square wide
    # enough for the archetype trends (documented as NOT the superellipse footprint).
    n_xy = max(24, 2 * nb)
    grid = ob.BlockGrid(nx=n_xy, ny=n_xy, nz=nb, dx=20.0, dy=20.0, dz=bench_h)
    dep = ob.make_deposit(grid, archetype, gseed, peak_grade=peak_grade,
                          name=f"{spec.name}-geology")
    econ = ob.Econ(price=price, recovery=recovery, mining_cost=mining_cost,
                   processing_cost=processing_cost)
    values = ob.block_values(dep, econ)
    prec = ob.build_precedence(grid, slope_deg)
    pit = ob.solve_upit(values, prec)
    cutoff = ob.cutoff_grade(econ)

    # per-level in-pit statistics (level convention: oreblocks levels go UP; spec benches go DOWN
    # from the rim, bench 1 = first bench below the rim ... bench nb = pit floor).
    per = grid.nx * grid.ny
    level_stats: dict[int, dict] = {}
    for level in range(grid.nz):
        sl = slice(level * per, (level + 1) * per)
        in_pit = pit.in_pit[sl]
        if not in_pit.any():
            level_stats[level] = {"grade": 0.0, "ore_fraction": 0.0, "tonnes": 0.0, "n": 0}
            continue
        g = dep.grade[sl][in_pit]
        t = dep.tonnage[sl][in_pit]
        tt = float(t.sum())
        level_stats[level] = {
            "grade": float((g * t).sum() / tt),
            "ore_fraction": float(t[g > cutoff].sum() / tt),
            "tonnes": tt,
            "n": int(in_pit.sum()),
        }

    shovel_bench = {int(k): int(v) for k, v in spec.topo.get("shovelBench", {}).items()}

    def bench_to_level(bench: int) -> int:
        # bench 1 (top) -> level nz-1 (surface); bench nb (floor) -> level 0
        return max(0, min(grid.nz - 1, grid.nz - bench))

    loaders = []
    for x in spec.loaders:
        d = dict(x)
        bench = shovel_bench.get(int(d["node_id"]), max(1, nb // 2))
        level = bench_to_level(bench)
        st = level_stats[level]
        d["face_bench"] = bench
        d["face_grade"] = round(st["grade"], 6)
        d["face_ore_fraction"] = round(st["ore_fraction"], 4)
        d["face_level_tonnes"] = round(st["tonnes"], 1)
        loaders.append(d)

    geology = {
        "schema": GEOLOGY_SCHEMA,
        "engine": "oreblocks",
        "archetype": archetype,
        "seed": gseed,
        "grid": {"nx": grid.nx, "ny": grid.ny, "nz": grid.nz,
                 "dx": grid.dx, "dy": grid.dy, "dz": grid.dz},
        "econ": {"price": price, "recovery": recovery,
                 "mining_cost": mining_cost, "processing_cost": processing_cost},
        "slope_deg": slope_deg,
        "cutoff_grade": round(cutoff, 8),
        "stamped_pit_value": round(pit.pit_value, 3),
        "stamped_n_in_pit": pit.n_in_pit,
        "per_level": {str(lv): {"grade": round(s["grade"], 6),
                                "ore_fraction": round(s["ore_fraction"], 4),
                                "tonnes": round(s["tonnes"], 1), "n": s["n"]}
                      for lv, s in level_stats.items()},
        "note": ("SYNTHETIC bench-aligned deposit (oreblocks); per-bench in-pit statistics of the "
                 "EXACT ultimate pit. Vertical axis matches the pit's benches; the horizontal "
                 "footprint is generic, not the superellipse."),
    }
    materials = dict(spec.materials)
    materials["geology"] = geology
    params = dict(spec.params)
    params["geology_archetype"] = archetype
    params["geology_seed"] = gseed
    return replace(spec, loaders=tuple(loaders), materials=materials, params=params)
