"""Varied open-pit scenario generator: every sampled axis changes the mine STRUCTURALLY.

`generate_open_pit(params, seed)` samples the design axes below from the seeded "geometry" /
"fleet" streams, builds the pit (geometry.openpit), sizes the fleet to a target match factor, and
gates the result through every validity check (scenarios.validate). A failing attempt resamples
(fresh substreams per attempt); after `max_attempts` it raises `GenerationError` naming the
failing checks — never a silently degenerate scenario.

Sampled axes (blueprint table): depth 6..20 benches | bench height {10,12,15} | berm 8..15 m |
face angle 60..75 deg | superellipse exponent 1.7..2.6, eccentricity 1..1.9, azimuth, radial
harmonics k=2..4 (|a| <= 0.12) | phases 1..3 with sector boosts | ramp style spiral / switchback /
dual_spiral, grade 8..10%, lanes 1..2 | zone policy | shovels 2..8 deep-weighted, <= 2 per bench |
1..2 crushers + 1..3 waste dumps + optional stockpile at azimuth/400..2500 m | 1..3 surface
junctions | fleet: 1..3 truck classes sized to target MF 0.7..1.5.

Determinism: same (params, seed) -> byte-identical MineSpec JSON. All sampling comes from
RngManager named streams; the geometry builder itself is RNG-free.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields

import numpy as np

from ..geometry.openpit import (FLOOR_R_MIN_M, OpenPitDesign, PitGeometryError, RimShape,
                                build_open_pit)
from ..io.topospec import fit_ellipse_axes
from ..network.constraints import ZonePolicy
from ..rng import RngManager
from .spec import MineSpec
from .validate import (diversity_signature, load_time_s, representative_cycle_s,
                       static_match_factor, validate_spec)

SURFACE_TRUCK_CLASSES = ("CAT_777G", "CAT_785D", "CAT_793F")
OPENPIT_LOADER_CLASSES = ("SHOVEL_25", "SHOVEL_45")
MAX_SHOVELS = 8
TRUCK_COUNT_BOUNDS = (4, 48)


class GenerationError(RuntimeError):
    """No valid scenario within max_attempts; message names the failing checks per attempt."""


@dataclass(frozen=True)
class OpenPitParams:
    """Optional per-axis overrides; every None is sampled from its blueprint range."""
    name: str | None = None
    n_benches: int | None = None
    bench_height_m: float | None = None
    berm_width_m: float | None = None
    face_angle_deg: float | None = None
    superellipse_n: float | None = None
    eccentricity: float | None = None
    n_phases: int | None = None
    ramp_style: str | None = None            # spiral | switchback | dual_spiral
    ramp_grade_pct: float | None = None
    ramp_lanes: int | None = None
    zone_policy: str | None = None
    n_shovels: int | None = None
    n_crushers: int | None = None
    n_waste_dumps: int | None = None
    stockpile: bool | None = None
    n_surface_junctions: int | None = None
    target_match_factor: float | None = None
    truck_classes: tuple[str, ...] | None = None

    def overrides(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)
                if getattr(self, f.name) is not None}


def _pick(override, sampler):
    return override if override is not None else sampler()


def _sample_design(p: OpenPitParams, geo: np.random.Generator) -> OpenPitDesign:
    nb = int(_pick(p.n_benches, lambda: int(geo.integers(6, 21))))
    h = float(_pick(p.bench_height_m, lambda: float(geo.choice([10.0, 12.0, 15.0]))))
    berm = float(_pick(p.berm_width_m, lambda: float(geo.uniform(8.0, 15.0))))
    face_angle = float(_pick(p.face_angle_deg, lambda: float(geo.uniform(60.0, 75.0))))
    step_in = berm + h / math.tan(math.radians(face_angle))

    n_exp = float(_pick(p.superellipse_n, lambda: float(geo.uniform(1.7, 2.6))))
    ecc = float(_pick(p.eccentricity, lambda: float(geo.uniform(1.0, 1.9))))
    azim = float(geo.uniform(0.0, 2 * math.pi))
    harmonics = tuple(
        (k, float(geo.uniform(-0.12, 0.12)), float(geo.uniform(0.0, 2 * math.pi)))
        for k in (2, 3, 4))
    n_phases = int(_pick(p.n_phases, lambda: int(geo.integers(1, 4))))
    boosts = tuple(
        (float(geo.uniform(0.0, 2 * math.pi)), float(geo.uniform(0.6, 1.6)),
         float(geo.uniform(0.08, 0.25)))
        for _ in range(n_phases - 1))
    pert = sum(abs(a) for _, a, _ in harmonics) + sum(b for _, _, b in boosts)
    # size the rim so the floor keeps its minimum radius with margin: the superellipse polar
    # radius can pinch to ~0.93*min(rx,ry) at the diagonals when n < 2
    floor_extra = float(geo.uniform(15.0, 120.0))
    ry = (FLOOR_R_MIN_M + floor_extra + nb * step_in) / max(1e-6, (1.0 - pert)) / 0.93
    rx = ry * ecc
    shape = RimShape(rx=rx, ry=ry, n_exp=n_exp, azimuth_rad=azim,
                     harmonics=harmonics, sector_boosts=boosts)

    style = str(_pick(p.ramp_style,
                      lambda: str(geo.choice(["spiral", "switchback", "dual_spiral"]))))
    grade = float(_pick(p.ramp_grade_pct, lambda: float(geo.uniform(8.0, 10.0))))
    lanes = int(_pick(p.ramp_lanes,
                      lambda: int(geo.choice([1, 2], p=[0.35, 0.65]))))
    policy = ZonePolicy(str(_pick(p.zone_policy, lambda: str(geo.choice(
        ["lockout", "loaded_priority", "group_batching"])))))
    ccw = bool(geo.integers(0, 2))
    entry = float(geo.uniform(0.0, 2 * math.pi))
    entry2 = float(entry + math.pi + geo.uniform(-0.6, 0.6)) if style == "dual_spiral" else None

    # faces: deep-weighted benches, <= 2 per bench; phase p >= 2 faces sit inside their sector
    n_shovels = int(_pick(p.n_shovels,
                          lambda: int(geo.integers(2, min(MAX_SHOVELS, 2 * nb) + 1))))
    weights = np.array([float(i) ** 1.5 for i in range(1, nb + 1)])
    weights /= weights.sum()
    per_bench: dict[int, int] = {}
    faces: list[tuple[int, float]] = []
    for s in range(n_shovels):
        for _ in range(64):
            b = int(geo.choice(np.arange(1, nb + 1), p=weights))
            if per_bench.get(b, 0) < 2:
                per_bench[b] = per_bench.get(b, 0) + 1
                break
        else:
            b = 1 + int(np.argmin([per_bench.get(i, 0) for i in range(1, nb + 1)]))
            per_bench[b] = per_bench.get(b, 0) + 1
        phase_of_face = s % n_phases
        if phase_of_face == 0 or not boosts:
            az = float(geo.uniform(0.0, 2 * math.pi))
        else:
            c, w, _ = boosts[phase_of_face - 1]
            az = float(c + geo.uniform(-w / 2, w / 2))
        faces.append((b, az))

    n_crush = int(_pick(p.n_crushers, lambda: int(geo.integers(1, 3))))
    n_waste = int(_pick(p.n_waste_dumps, lambda: int(geo.integers(1, 4))))
    stock = bool(_pick(p.stockpile, lambda: bool(geo.random() < 0.4)))
    dests: list[tuple[str, float, float]] = []
    for _ in range(n_crush):
        dests.append(("crusher", float(geo.uniform(0.0, 2 * math.pi)),
                      float(geo.uniform(400.0, 2500.0))))
    for _ in range(n_waste):
        dests.append(("dump", float(geo.uniform(0.0, 2 * math.pi)),
                      float(geo.uniform(400.0, 2500.0))))
    if stock:
        dests.append(("stockpile", float(geo.uniform(0.0, 2 * math.pi)),
                      float(geo.uniform(400.0, 2500.0))))
    n_junc = int(_pick(p.n_surface_junctions, lambda: int(geo.integers(1, 4))))

    return OpenPitDesign(
        shape=shape, n_benches=nb, bench_height_m=h, berm_width_m=berm,
        face_angle_deg=face_angle, ramp_style=style, ramp_grade_pct=grade, ramp_lanes=lanes,
        spiral_ccw=ccw, entry_azimuth_rad=entry, entry_azimuth2_rad=entry2, zone_policy=policy,
        faces=tuple(faces), destinations=tuple(dests), n_surface_junctions=n_junc)


def _assemble_spec(p: OpenPitParams, seed: int, design: OpenPitDesign,
                   fleet: np.random.Generator) -> MineSpec | None:
    """Build geometry, size the fleet to the target MF, assemble the frozen document.
    Returns None when the sized fleet cannot be estimated (unroutable) — caller resamples."""
    geo_built = build_open_pit(design)
    net = geo_built.network
    assert net is not None

    loader_classes = [str(fleet.choice(OPENPIT_LOADER_CLASSES,
                                       p=[0.35, 0.65] if design.n_benches >= 12 else [0.6, 0.4]))
                      for _ in geo_built.face_nodes]
    loaders = tuple({"node_id": fid, "loader_class": lc, "n_spots": 1}
                    for fid, lc in zip(sorted(geo_built.face_nodes), loader_classes))
    dumps = tuple(geo_built.all_dump_nodes)

    classes = p.truck_classes
    if classes is None:
        k = int(fleet.integers(1, 4))
        idx = sorted(fleet.choice(len(SURFACE_TRUCK_CLASSES), size=k, replace=False).tolist())
        classes = tuple(SURFACE_TRUCK_CLASSES[i] for i in idx)
    target_mf = float(_pick(p.target_match_factor, lambda: float(fleet.uniform(0.7, 1.5))))

    # provisional roster (1 truck of the biggest class) to estimate the representative cycle
    probe = MineSpec(kind="openpit", name="probe", seed=seed, params={}, network=net.to_dict(),
                     zones=tuple(z.to_dict() for z in geo_built.zones.values()),
                     junctions=tuple(j.to_dict() for j in geo_built.junctions.values()),
                     loaders=loaders, dumps=dumps,
                     trucks=({"truck_id": 1, "unit_name": classes[-1],
                              "start_loader": loaders[0]["node_id"]},))
    cycle_s = representative_cycle_s(probe.to_runtime())
    if cycle_s is None:
        return None
    ls_deepest = min(loaders, key=lambda x: net.nodes[x["node_id"]].pos[2])
    load_s = load_time_s(str(ls_deepest["loader_class"]), classes[-1])
    n_trucks = int(round(target_mf * len(loaders) * cycle_s / load_s))
    n_trucks = max(TRUCK_COUNT_BOUNDS[0], min(TRUCK_COUNT_BOUNDS[1], n_trucks))

    face_ids = [x["node_id"] for x in loaders]
    trucks = tuple({"truck_id": i + 1, "unit_name": classes[i % len(classes)],
                    "start_loader": face_ids[i % len(face_ids)]} for i in range(n_trucks))

    rim = geo_built.rings[0][:, :2]
    rim_rx, rim_ry = fit_ellipse_axes(rim, (0.0, 0.0))
    topo = {
        "center": [0.0, 0.0], "rimRx": round(rim_rx, 1), "rimRy": round(rim_ry, 1),
        "nBenches": design.n_benches, "benchHeightM": design.bench_height_m,
        "benchWidthM": round(design.berm_width_m, 1),
        "faceAngleDeg": round(design.face_angle_deg, 1),
        "rampWidthM": design.ramp_width_m,
        "shovelBench": {str(fid): b for fid, b in sorted(geo_built.shovel_bench.items())},
    }
    params = {
        "n_benches": design.n_benches, "bench_height_m": design.bench_height_m,
        "berm_width_m": round(design.berm_width_m, 3),
        "face_angle_deg": round(design.face_angle_deg, 3),
        "step_in_m": round(design.step_in_m, 3), "depth_m": design.depth_m,
        "superellipse_n": round(design.shape.n_exp, 3),
        "rim_rx_m": round(design.shape.rx, 1), "rim_ry_m": round(design.shape.ry, 1),
        "rim_azimuth_rad": round(design.shape.azimuth_rad, 4),
        "harmonics": [[k, round(a, 4), round(phi, 4)] for k, a, phi in design.shape.harmonics],
        "sector_boosts": [[round(c, 4), round(w, 4), round(b, 4)]
                          for c, w, b in design.shape.sector_boosts],
        "n_phases": 1 + len(design.shape.sector_boosts),
        "ramp_style": design.ramp_style, "ramp_grade_pct": round(design.ramp_grade_pct, 3),
        "ramp_lanes": design.ramp_lanes, "zone_policy": design.zone_policy.value,
        "spiral_ccw": design.spiral_ccw,
        "entry_azimuth_rad": round(design.entry_azimuth_rad, 4),
        "n_shovels": len(design.faces),
        "faces": [[b, round(az, 4)] for b, az in design.faces],
        "destinations": [[k, round(az, 4), round(d, 1)] for k, az, d in design.destinations],
        "n_surface_junctions": design.n_surface_junctions,
        "truck_classes": list(classes), "target_match_factor": round(target_mf, 3),
        "n_trucks": n_trucks,
        "floor_r_min_m": round(geo_built.floor_r_min_m, 2),
    }
    rt_cycle = cycle_s
    name = p.name or f"openpit-{seed}"
    spec = MineSpec(
        kind="openpit", name=name, seed=seed, params=params, network=net.to_dict(),
        zones=tuple(z.to_dict() for z in sorted(geo_built.zones.values(), key=lambda z: z.id)),
        junctions=tuple(j.to_dict() for j in sorted(geo_built.junctions.values(),
                                                    key=lambda j: j.id)),
        loaders=loaders, dumps=dumps, trucks=trucks, topo=topo,
        est={"cycle_s": round(rt_cycle, 1),
             "match_factor": round(static_match_factor(spec_mf_rt(net, loaders, dumps, trucks),
                                                       rt_cycle), 3),
             "load_s": round(load_s, 1)})
    return spec


def spec_mf_rt(net, loaders, dumps, trucks):
    """Runtime view for the MF estimate without re-serializing (internal helper)."""
    from ..des.sim import LoaderSpec, TruckSpec
    from .spec import RuntimeBundle
    return RuntimeBundle(
        net=net, zones={}, junctions={},
        loaders=[LoaderSpec(x["node_id"], x["loader_class"], x["n_spots"]) for x in loaders],
        dumps=list(dumps),
        trucks=[TruckSpec(t["truck_id"], t["unit_name"], t["start_loader"]) for t in trucks])


def generate_open_pit(params: OpenPitParams | None = None, seed: int = 0,
                      max_attempts: int = 25) -> MineSpec:
    """Sample, build, size and VALIDATE one open-pit scenario (module docstring)."""
    p = params or OpenPitParams()
    rng = RngManager(seed)
    failures: list[str] = []
    for attempt in range(max_attempts):
        geo_stream = rng.stream(f"geometry/{attempt}")
        fleet_stream = rng.stream(f"fleet/{attempt}")
        try:
            design = _sample_design(p, geo_stream)
            spec = _assemble_spec(p, seed, design, fleet_stream)
        except PitGeometryError as e:
            failures.append(f"attempt {attempt}: geometry_buildable: {e}")
            continue
        if spec is None:
            failures.append(f"attempt {attempt}: fleet_sizing: representative cycle unroutable")
            continue
        report = validate_spec(spec)
        if report.ok:
            return spec
        failures.append(f"attempt {attempt}: " + ", ".join(report.failing()))
    raise GenerationError(
        f"no valid open-pit scenario for seed {seed} in {max_attempts} attempts: "
        + "; ".join(failures[-5:]))


def generate_batch(n: int, seed: int, params: OpenPitParams | None = None,
                   ensure_diverse: bool = True, max_seed_bumps: int = 20) -> list[MineSpec]:
    """Generate n scenarios with deterministic child seeds; with ensure_diverse, a spec whose
    structural signature repeats within the batch is regenerated under a bumped child seed."""
    rng = RngManager(seed)
    out: list[MineSpec] = []
    seen: set[tuple] = set()
    for i in range(n):
        child = int(rng.stream(f"batch/{i}").integers(0, 2**31 - 1))
        for bump in range(max_seed_bumps + 1):
            spec = generate_open_pit(params, seed=child + bump)
            sig = diversity_signature(spec)
            if not ensure_diverse or sig not in seen:
                seen.add(sig)
                out.append(spec.with_name(spec.name if params and params.name
                                          else f"openpit-b{seed}-{i}"))
                break
        else:
            raise GenerationError(
                f"batch item {i}: could not find a structurally distinct scenario "
                f"in {max_seed_bumps} seed bumps")
    return out
