"""Varied underground scenario generator (blueprint 8.3): multi-level mines with decline traffic
as the structural bottleneck, three material-flow modes, and the LHD/ore-pass coupling.

Sampled axes: n_levels 3..10 | level spacing 25..60 m | first level 60..120 m | decline style
spiral/zigzag, grade 1:8..1:6.5 | passing-bay spacing 150..400 m | zone policy (lockout /
loaded_priority / group_batching) | shaft present | 1..3 ore passes with sampled level spans +
capacities | 1..4 drifts per level (80..350 m, 1..3 drawpoints) | flow_mode (lhd_orepass_truck /
truck_direct / truck_shaft) | 1..2 LHDs per producing level | UG truck classes, fleet sized to a
target match factor against the representative decline cycle.

Loading points by flow mode (IO contract 9.1 — shovels are whatever the TRUCKS load at):
    lhd_orepass_truck  chutes (CHUTE class); LHDs feed them through pass inventories
    truck_shaft        same, but trucks dump at the shaft bin (short underground cycle)
    truck_direct       drift stubs (LHD_*_LOADING class); no passes, no LHD agents

Same discipline as the open-pit generator: resample with NAMED failures, every spec gated by
scenarios.validate before it leaves.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields

import numpy as np

from ..geometry.underground import (DriftSpec, LevelSpec, OrePassSpec as GeoOrePassSpec,
                                    UndergroundDesign, UndergroundGeometry,
                                    UndergroundGeometryError, build_underground)
from ..network.constraints import ZonePolicy
from ..rng import RngManager
from .openpit_gen import GenerationError
from .spec import MineSpec
from .validate import (diversity_signature, load_time_s, representative_cycle_s,
                       static_match_factor, validate_spec)

UG_TRUCK_CLASSES = ("UG_TRUCK_50", "UG_TRUCK_63")
LHD_CLASSES = ("LHD_14", "LHD_18")
TRUCK_COUNT_BOUNDS = (2, 20)


@dataclass(frozen=True)
class UndergroundParams:
    """Optional per-axis overrides; every None is sampled from its blueprint range."""
    name: str | None = None
    n_levels: int | None = None
    level_spacing_m: float | None = None
    first_level_depth_m: float | None = None
    decline_style: str | None = None
    decline_grade_pct: float | None = None
    passing_bay_spacing_m: float | None = None
    zone_policy: str | None = None
    flow_mode: str | None = None
    n_orepasses: int | None = None
    shaft: bool | None = None
    target_match_factor: float | None = None
    truck_classes: tuple[str, ...] | None = None

    def overrides(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)
                if getattr(self, f.name) is not None}


def _pick(override, sampler):
    return override if override is not None else sampler()


def _sample_design(p: UndergroundParams, geo: np.random.Generator) -> UndergroundDesign:
    n_levels = int(_pick(p.n_levels, lambda: int(geo.integers(3, 11))))
    spacing = float(_pick(p.level_spacing_m, lambda: float(geo.uniform(25.0, 60.0))))
    first = float(_pick(p.first_level_depth_m, lambda: float(geo.uniform(60.0, 120.0))))
    style = str(_pick(p.decline_style, lambda: str(geo.choice(["spiral", "zigzag"]))))
    grade = float(_pick(p.decline_grade_pct, lambda: float(geo.uniform(12.5, 15.38))))
    bays = float(_pick(p.passing_bay_spacing_m, lambda: float(geo.uniform(150.0, 400.0))))
    policy = ZonePolicy(str(_pick(p.zone_policy, lambda: str(geo.choice(
        ["lockout", "loaded_priority", "group_batching"])))))
    flow = str(_pick(p.flow_mode, lambda: str(geo.choice(
        ["lhd_orepass_truck", "truck_direct", "truck_shaft"]))))
    shaft = bool(_pick(p.shaft, lambda: bool(geo.random() < 0.35)))
    if flow == "truck_shaft":
        shaft = True

    # truck_direct: every drift stub is a LOADING point (a cyclelog shovel needing its own LHD
    # + truck stream over ONE decline) — one active heading per level keeps the match factor
    # physically reachable. LHD flows concentrate loading at 1..3 chutes, so multiple drifts
    # per level are fine there.
    max_drifts = 2 if flow == "truck_direct" else 5
    levels = tuple(
        LevelSpec(drifts=tuple(
            DriftSpec(length_m=float(geo.uniform(80.0, 350.0)),
                      n_drawpoints=int(geo.integers(1, 4)),
                      azimuth_rad=float(geo.uniform(0.0, 2 * math.pi)))
            for _ in range(int(geo.integers(1, max_drifts)))))
        for _ in range(n_levels))

    passes: tuple[GeoOrePassSpec, ...] = ()
    if flow in ("lhd_orepass_truck", "truck_shaft"):
        n_passes = int(_pick(p.n_orepasses, lambda: int(geo.integers(1, 4))))
        upper_max = n_levels - 2                          # tips live above the haulage level
        specs = []
        for _ in range(n_passes):
            top = int(geo.integers(0, upper_max + 1))
            bottom = int(geo.integers(top, upper_max + 1))
            specs.append(GeoOrePassSpec(
                top_level=top, bottom_level=bottom,
                capacity_t=float(geo.uniform(200.0, 600.0)),
                azimuth_rad=float(geo.uniform(0.0, 2 * math.pi)),
                tip_offset_m=float(geo.uniform(45.0, 90.0))))
        passes = tuple(specs)

    return UndergroundDesign(
        n_levels=n_levels, first_level_depth_m=first, level_spacing_m=spacing,
        decline_style=style, decline_grade_pct=grade,
        spiral_radius_m=float(geo.uniform(25.0, 40.0)),
        passing_bay_spacing_m=bays, zone_policy=policy, levels=levels, ore_passes=passes,
        shaft=shaft, shaft_bin_capacity_t=float(geo.uniform(300.0, 500.0)),
        shaft_hoist_tph=float(geo.uniform(400.0, 800.0)), flow_mode=flow)


def _rosters(design: UndergroundDesign, geo_built: UndergroundGeometry,
             fleet: np.random.Generator, p: UndergroundParams
             ) -> tuple[tuple, tuple, tuple, dict]:
    """(loaders, lhds, dump list, materials) for the sampled design."""
    materials: dict = {}
    lhds: list[dict] = []
    if design.flow_mode == "truck_direct":
        stubs = sorted(s for lvl in geo_built.drift_stubs.values() for s in lvl)
        lhd_cls = [str(fleet.choice(LHD_CLASSES)) for _ in stubs]
        loaders = tuple({"node_id": s, "loader_class": f"{c}_LOADING", "n_spots": 1}
                        for s, c in zip(stubs, lhd_cls))
        dumps = (geo_built.surface_dump_id,)
    else:
        loaders = tuple({"node_id": c, "loader_class": "CHUTE", "n_spots": 1}
                        for _, c in sorted(geo_built.chutes.items()))
        materials["ore_passes"] = [
            {"pass_id": pi, "chute_node": geo_built.chutes[pi],
             "capacity_t": round(design.ore_passes[pi].capacity_t, 1)}
            for pi in sorted(geo_built.chutes)]
        # LHDs: 1..2 per spanned level, each feeding the FIRST pass that spans its level
        lhd_id = 1
        for lvl in range(design.n_levels - 1):
            feeding = [pi for pi, op in enumerate(design.ore_passes)
                       if op.top_level <= lvl <= op.bottom_level]
            if not feeding or not geo_built.drawpoints.get(lvl):
                continue
            for _ in range(int(fleet.integers(1, 3))):
                lhds.append({"lhd_id": lhd_id, "unit_name": str(fleet.choice(LHD_CLASSES)),
                             "drawpoints": list(geo_built.drawpoints[lvl]),
                             "tip_node": geo_built.tips[feeding[0]][lvl],
                             "pass_id": feeding[0]})
                lhd_id += 1
        if design.flow_mode == "truck_shaft":
            dumps = (geo_built.bin_id,)
        else:
            dumps = (geo_built.surface_dump_id,)
    if design.shaft and geo_built.bin_id is not None:
        materials["shaft_bin"] = {"node": geo_built.bin_id,
                                  "capacity_t": round(design.shaft_bin_capacity_t, 1),
                                  "hoist_tph": round(design.shaft_hoist_tph, 1)}
    return loaders, tuple(lhds), dumps, materials


def _assemble_spec(p: UndergroundParams, seed: int, design: UndergroundDesign,
                   fleet: np.random.Generator) -> MineSpec | None:
    geo_built = build_underground(design)
    net = geo_built.network
    assert net is not None
    loaders, lhds, dumps, materials = _rosters(design, geo_built, fleet, p)
    if not loaders:
        return None

    classes = p.truck_classes
    if classes is None:
        k = int(fleet.integers(1, 3))
        idx = sorted(fleet.choice(len(UG_TRUCK_CLASSES), size=k, replace=False).tolist())
        classes = tuple(UG_TRUCK_CLASSES[i] for i in idx)
    target_mf = float(_pick(p.target_match_factor, lambda: float(fleet.uniform(0.7, 1.5))))

    probe = MineSpec(kind="underground", name="probe", seed=seed, params={},
                     network=net.to_dict(),
                     zones=tuple(z.to_dict() for z in geo_built.zones.values()),
                     junctions=tuple(j.to_dict() for j in geo_built.junctions.values()),
                     loaders=loaders, dumps=dumps,
                     trucks=({"truck_id": 1, "unit_name": classes[-1],
                              "start_loader": loaders[0]["node_id"]},))
    cycle_s = representative_cycle_s(probe.to_runtime())
    if cycle_s is None:
        return None
    load_s = load_time_s(str(loaders[0]["loader_class"]), classes[-1])
    n_trucks = int(round(target_mf * len(loaders) * cycle_s / load_s))
    n_trucks = max(TRUCK_COUNT_BOUNDS[0], min(TRUCK_COUNT_BOUNDS[1], n_trucks))
    loader_ids = [x["node_id"] for x in loaders]
    trucks = tuple({"truck_id": i + 1, "unit_name": classes[i % len(classes)],
                    "start_loader": loader_ids[i % len(loader_ids)]} for i in range(n_trucks))

    params = {
        "n_levels": design.n_levels, "level_spacing_m": round(design.level_spacing_m, 2),
        "first_level_depth_m": round(design.first_level_depth_m, 2),
        "depth_m": round(-design.level_z(design.haulage_level), 2),
        "access_style": design.decline_style,
        "decline_grade_pct": round(design.decline_grade_pct, 3),
        "passing_bay_spacing_m": round(design.passing_bay_spacing_m, 1),
        "zone_policy": design.zone_policy.value, "flow_mode": design.flow_mode,
        "n_orepasses": len(design.ore_passes), "shaft": design.shaft,
        "n_drifts_total": sum(len(lv.drifts) for lv in design.levels),
        "n_drawpoints_total": sum(len(v) for v in geo_built.drawpoints.values()),
        "n_lhds": len(lhds), "truck_classes": list(classes),
        "target_match_factor": round(target_mf, 3), "n_trucks": n_trucks,
        "n_surface_junctions": 0,
    }
    topo = {"schema": "minehaulsim.minetopo/v1", **geo_built.minetopo_payload()}
    name = p.name or f"underground-{seed}"
    return MineSpec(
        kind="underground", name=name, seed=seed, params=params, network=net.to_dict(),
        zones=tuple(z.to_dict() for z in sorted(geo_built.zones.values(), key=lambda z: z.id)),
        junctions=tuple(j.to_dict() for j in sorted(geo_built.junctions.values(),
                                                    key=lambda j: j.id)),
        loaders=loaders, dumps=dumps, trucks=trucks, topo=topo,
        est={"cycle_s": round(cycle_s, 1),
             "match_factor": round(static_match_factor(_mf_rt(net, loaders, dumps, trucks),
                                                       cycle_s), 3),
             "load_s": round(load_s, 1)},
        lhds=lhds, materials=materials)


def _mf_rt(net, loaders, dumps, trucks):
    from ..des.sim import LoaderSpec, TruckSpec
    from .spec import RuntimeBundle
    return RuntimeBundle(
        net=net, zones={}, junctions={},
        loaders=[LoaderSpec(x["node_id"], x["loader_class"], x["n_spots"]) for x in loaders],
        dumps=list(dumps),
        trucks=[TruckSpec(t["truck_id"], t["unit_name"], t["start_loader"]) for t in trucks])


def generate_underground(params: UndergroundParams | None = None, seed: int = 0,
                         max_attempts: int = 25) -> MineSpec:
    """Sample, build, size and VALIDATE one underground scenario (module docstring)."""
    p = params or UndergroundParams()
    rng = RngManager(seed)
    failures: list[str] = []
    for attempt in range(max_attempts):
        geo_stream = rng.stream(f"ug.geometry/{attempt}")
        fleet_stream = rng.stream(f"ug.fleet/{attempt}")
        try:
            design = _sample_design(p, geo_stream)
            spec = _assemble_spec(p, seed, design, fleet_stream)
        except UndergroundGeometryError as e:
            failures.append(f"attempt {attempt}: geometry_buildable: {e}")
            continue
        if spec is None:
            failures.append(f"attempt {attempt}: fleet_sizing: unroutable or no loading points")
            continue
        report = validate_spec(spec)
        if report.ok:
            return spec
        failures.append(f"attempt {attempt}: " + ", ".join(report.failing()))
    raise GenerationError(
        f"no valid underground scenario for seed {seed} in {max_attempts} attempts: "
        + "; ".join(failures[-5:]))


def generate_underground_batch(n: int, seed: int, params: UndergroundParams | None = None,
                               ensure_diverse: bool = True,
                               max_seed_bumps: int = 20) -> list[MineSpec]:
    rng = RngManager(seed)
    out: list[MineSpec] = []
    seen: set[tuple] = set()
    for i in range(n):
        child = int(rng.stream(f"ug.batch/{i}").integers(0, 2**31 - 1))
        for bump in range(max_seed_bumps + 1):
            spec = generate_underground(params, seed=child + bump)
            sig = diversity_signature(spec)
            if not ensure_diverse or sig not in seen:
                seen.add(sig)
                out.append(spec.with_name(spec.name if params and params.name
                                          else f"underground-b{seed}-{i}"))
                break
        else:
            raise GenerationError(f"batch item {i}: no structurally distinct scenario "
                                  f"in {max_seed_bumps} seed bumps")
    return out
