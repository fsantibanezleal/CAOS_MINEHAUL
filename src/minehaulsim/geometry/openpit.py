"""Parametric open-pit geometry: the layer that makes every generated pit STRUCTURALLY different.

Following the pit-design literature (Espejo/Nancel-Penard/Morales 2019 on ramp design in
block-model pit optimization; US9589076 area-based pit designers), a pit solid is derived from:

- **Rim**: a superellipse `|x/rx|^n + |y/ry|^n = 1` (exponent n in [1.7, 2.6], azimuth-rotated)
  times a low-order radial perturbation `1 + sum_k a_k cos(k*theta + phi_k)` (k = 2..4,
  |a_k| <= 0.12) plus raised-cosine sector boosts for phase/pushback expansions. The polar form
  keeps every ring star-shaped (simple, never self-intersecting) while killing the
  same-ellipse-every-time silhouette.
- **Benches**: ring i = rim shrunk radially inward by `i * step_in` at elevation `-i * bench_h`,
  with `step_in = berm_width + bench_height / tan(face_angle)`.
- **Ramp**: one of three styles, split into piecewise-constant-grade Segments at generation time:
    spiral       helical wall-hugging descent; azimuth advances so that ds_horizontal * grade
                 integrates one bench height between bench crossings
    switchback   zigzag confined to a wall sector; each 180-degree turn is modeled as a
                 capacity-1 Junction (cross_s = TURN_CROSS_S) at the bench crossing node
    dual_spiral  two independent spirals; with ramp_lanes=1 they become a ONE-WAY circulation
                 pair (one descent-only, one climb-only)
  Single-lane spiral/switchback ramps (ramp_lanes=1) mark their segments `single_lane_op` and
  chain one DirectionZone per segment: opposing traffic arbitrates at every bench crossing
  (the berm is the passing bay), which is how narrow real ramps are operated.
- **Faces**: shovel positions (bench, azimuth) connected along the bench arc to the nearest ramp
  crossing on that bench.
- **Ex-pit**: crushers / waste dumps / optional stockpile at rim-exterior azimuth+distance, tied
  to the portal(s) by a shared surface trunk with 1..3 Junction conflict points.

Deterministic and RNG-free: `build_open_pit(design)` is a pure function of `OpenPitDesign`; the
scenario generator owns all sampling. Raises `PitGeometryError` for unbuildable parameter sets
(the generator's resample signal).

Node id blocks (all far below the planning layer's 100_000 base):
    faces 1..S (= cyclelog shovel ids), destinations 101.. (dump ids), infrastructure 1000..
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..network.constraints import DirectionZone, Junction, ZonePolicy, segment_capacity
from ..network.graph import NodeSite, RoadNetwork, Segment
from .paths import polyline_length

FLOOR_R_MIN_M = 40.0
N_RING_PTS = 144                      # ring sampling (2.5 degrees)
RAMP_SPEED_KMH = 40.0
SWITCHBACK_SPEED_KMH = 30.0
TURN_CROSS_S = 15.0                   # 180-degree switchback turn = capacity-1 junction hold
BENCH_SPEED_KMH = 30.0
SURFACE_SPEED_KMH = 60.0
IN_PIT_RR_PCT = 2.5
SURFACE_RR_PCT = 2.0
SURFACE_JUNCTION_CROSS_S = 12.0
MIN_BENCH_ARC_M = 10.0                # a face is nudged along its bench if it lands on the crossing
MAX_SWITCHBACK_SWEEP_RAD = 1.9        # a leg sweeping wider than this leaves its wall sector

FACE_ID_BASE = 1
DEST_ID_BASE = 101
INFRA_ID_BASE = 1_000


class PitGeometryError(ValueError):
    """Parameter set cannot produce a buildable pit; scenario generators resample on this."""


@dataclass(frozen=True)
class RimShape:
    """Polar rim model: superellipse base times radial perturbation (module docstring)."""
    rx: float
    ry: float
    n_exp: float
    azimuth_rad: float = 0.0
    harmonics: tuple[tuple[int, float, float], ...] = ()          # (k, amplitude, phase)
    sector_boosts: tuple[tuple[float, float, float], ...] = ()    # (center, width, boost) rad/rad/-

    def __post_init__(self) -> None:
        if min(self.rx, self.ry) <= 0 or self.n_exp <= 0:
            raise PitGeometryError("rim axes and exponent must be positive")
        if self.perturbation_bound() >= 0.9:
            raise PitGeometryError("radial perturbation too large: rings would not stay simple")

    def perturbation_bound(self) -> float:
        """Upper bound on |relative radial perturbation| (harmonics + boosts)."""
        return (sum(abs(a) for _, a, _ in self.harmonics)
                + sum(b for _, _, b in self.sector_boosts))

    def radius(self, theta: np.ndarray | float) -> np.ndarray | float:
        """Rim radius r(theta) [m] at world azimuth(s) theta."""
        th = np.asarray(theta, dtype=np.float64)
        t = th - self.azimuth_rad
        c, s = np.abs(np.cos(t)), np.abs(np.sin(t))
        base = ((c / self.rx) ** self.n_exp + (s / self.ry) ** self.n_exp) ** (-1.0 / self.n_exp)
        pert = np.ones_like(base)
        for k, a, phi in self.harmonics:
            pert = pert + a * np.cos(k * th + phi)
        for center, width, boost in self.sector_boosts:
            d = np.abs((th - center + math.pi) % (2 * math.pi) - math.pi)
            w = np.where(d < width / 2, np.cos(math.pi * d / width) ** 2, 0.0)
            pert = pert + boost * w
        out = base * pert
        return float(out) if np.isscalar(theta) else out


@dataclass(frozen=True)
class OpenPitDesign:
    """The frozen deterministic design document; `build_open_pit` is a pure function of it."""
    shape: RimShape
    n_benches: int
    bench_height_m: float
    berm_width_m: float
    face_angle_deg: float
    ramp_style: str                                   # spiral | switchback | dual_spiral
    ramp_grade_pct: float                             # positive, percent (descent magnitude)
    ramp_lanes: int                                   # 1 single-lane | 2 free passing
    ramp_width_m: float = 25.0
    spiral_ccw: bool = True
    entry_azimuth_rad: float = 0.0
    entry_azimuth2_rad: float | None = None           # dual_spiral second entry
    zone_policy: ZonePolicy = ZonePolicy.LOADED_PRIORITY
    faces: tuple[tuple[int, float], ...] = ()         # (bench 1..n_benches, azimuth) per shovel
    destinations: tuple[tuple[str, float, float], ...] = ()  # (kind, azimuth, dist_beyond_rim_m)
    n_surface_junctions: int = 1

    def __post_init__(self) -> None:
        if self.ramp_style not in ("spiral", "switchback", "dual_spiral"):
            raise PitGeometryError(f"unknown ramp_style {self.ramp_style!r}")
        if self.ramp_lanes not in (1, 2):
            raise PitGeometryError("ramp_lanes must be 1 or 2")
        if not (0 < self.ramp_grade_pct <= 12):
            raise PitGeometryError("ramp grade must be in (0, 12] percent")
        if self.n_benches < 1 or self.bench_height_m <= 0:
            raise PitGeometryError("need >= 1 bench of positive height")
        if self.ramp_style == "dual_spiral" and self.entry_azimuth2_rad is None:
            raise PitGeometryError("dual_spiral needs entry_azimuth2_rad")
        for b, _ in self.faces:
            if not (1 <= b <= self.n_benches):
                raise PitGeometryError(f"face bench {b} outside 1..{self.n_benches}")
        for kind, _, dist in self.destinations:
            if kind not in ("crusher", "dump", "stockpile"):
                raise PitGeometryError(f"unknown destination kind {kind!r}")
            if dist <= 0:
                raise PitGeometryError("destination distance must be positive")

    @property
    def step_in_m(self) -> float:
        return self.berm_width_m + self.bench_height_m / math.tan(math.radians(self.face_angle_deg))

    @property
    def depth_m(self) -> float:
        return self.n_benches * self.bench_height_m


@dataclass
class OpenPitGeometry:
    """Built pit: rendered solids + the derived constrained RoadNetwork and traffic specs."""
    design: OpenPitDesign
    rings: list[np.ndarray] = field(default_factory=list)         # rim..floor, each (N, 3)
    ramp_polylines: list[np.ndarray] = field(default_factory=list)
    network: RoadNetwork | None = None
    zones: dict[int, DirectionZone] = field(default_factory=dict)
    junctions: dict[int, Junction] = field(default_factory=dict)
    face_nodes: dict[int, tuple[int, float]] = field(default_factory=dict)  # id -> (bench, az)
    crusher_ids: list[int] = field(default_factory=list)
    dump_ids: list[int] = field(default_factory=list)
    stockpile_id: int | None = None
    portal_ids: list[int] = field(default_factory=list)
    shovel_bench: dict[int, int] = field(default_factory=dict)
    floor_r_min_m: float = 0.0

    @property
    def all_dump_nodes(self) -> list[int]:
        """Legal dump targets in cyclelog order: crushers first, then waste, then stockpile."""
        out = list(self.crusher_ids) + list(self.dump_ids)
        if self.stockpile_id is not None:
            out.append(self.stockpile_id)
        return out


# ---------------------------------------------------------------- ring + wall primitives

def _ring_radius(shape: RimShape, theta: np.ndarray | float, inset_m: float):
    return shape.radius(theta) - inset_m


def _wall_point(shape: RimShape, theta: float, depth_m: float, step_in_m: float,
                bench_h_m: float) -> np.ndarray:
    """Point on the pit wall at (azimuth, depth): radius shrinks continuously with depth."""
    r = _ring_radius(shape, theta, (depth_m / bench_h_m) * step_in_m)
    if r <= 0:
        raise PitGeometryError(f"wall radius non-positive at depth {depth_m:.0f} m")
    z = 0.0 if depth_m == 0 else -depth_m
    return np.array([r * math.cos(theta), r * math.sin(theta), z], dtype=np.float64)


def _build_rings(design: OpenPitDesign) -> list[np.ndarray]:
    th = np.linspace(0.0, 2 * math.pi, N_RING_PTS, endpoint=False)
    rings: list[np.ndarray] = []
    for i in range(design.n_benches + 1):
        r = _ring_radius(design.shape, th, i * design.step_in_m)
        if np.min(r) <= 0:
            raise PitGeometryError(f"ring {i} radius non-positive (pit too small for {i} benches)")
        z = 0.0 if i == 0 else -(i * design.bench_height_m)
        rings.append(np.stack([r * np.cos(th), r * np.sin(th), np.full_like(th, z)], axis=1))
    floor_r_min = float(np.min(_ring_radius(design.shape, th, design.n_benches * design.step_in_m)))
    if floor_r_min < FLOOR_R_MIN_M:
        raise PitGeometryError(f"floor radius {floor_r_min:.1f} m < {FLOOR_R_MIN_M} m minimum")
    return rings


# ---------------------------------------------------------------- ramp path builders

def _spiral_path(design: OpenPitDesign, entry_az: float, ccw: bool
                 ) -> tuple[list[np.ndarray], list[int]]:
    """Wall-hugging helix entry->floor. Returns (points, crossing indices per bench 0..n)."""
    grade = design.ramp_grade_pct / 100.0
    d_theta = 0.04 * (1.0 if ccw else -1.0)
    theta, depth = entry_az, 0.0
    pts = [_wall_point(design.shape, theta, 0.0, design.step_in_m, design.bench_height_m)]
    crossings = [0]
    next_bench = 1
    max_steps = 500_000
    for _ in range(max_steps):
        if next_bench > design.n_benches:
            break
        r_here = _ring_radius(design.shape, theta,
                              (depth / design.bench_height_m) * design.step_in_m)
        if r_here <= FLOOR_R_MIN_M / 2:
            raise PitGeometryError("spiral collapsed below minimum wall radius")
        d_depth = r_here * abs(d_theta) * grade
        target = next_bench * design.bench_height_m
        if depth + d_depth >= target - 1e-9:
            f = (target - depth) / d_depth
            theta += f * d_theta
            depth = target
            pts.append(_wall_point(design.shape, theta, depth, design.step_in_m,
                                   design.bench_height_m))
            crossings.append(len(pts) - 1)
            next_bench += 1
        else:
            theta += d_theta
            depth += d_depth
            pts.append(_wall_point(design.shape, theta, depth, design.step_in_m,
                                   design.bench_height_m))
    else:
        raise PitGeometryError("spiral did not reach the floor (step budget exhausted)")
    return pts, crossings


def _switchback_path(design: OpenPitDesign) -> tuple[list[np.ndarray], list[int]]:
    """Zigzag descent confined to the wall sector around entry_azimuth_rad. Each leg starts where
    the previous one turned (continuity), alternating sweep direction; the sweep grows as the wall
    radius shrinks with depth (same horizontal run per bench)."""
    grade = design.ramp_grade_pct / 100.0
    leg_run_m = design.bench_height_m / grade
    theta = design.entry_azimuth_rad
    direction = 1.0 if design.spiral_ccw else -1.0
    n_samples = 14
    pts: list[np.ndarray] = [
        _wall_point(design.shape, theta, 0.0, design.step_in_m, design.bench_height_m)]
    crossings: list[int] = [0]
    for bench in range(design.n_benches):
        r_mid = _ring_radius(design.shape, theta, (bench + 0.5) * design.step_in_m)
        if r_mid <= 0:
            raise PitGeometryError("switchback wall radius non-positive")
        sweep = leg_run_m / r_mid
        if sweep > MAX_SWITCHBACK_SWEEP_RAD:
            raise PitGeometryError(
                f"switchback leg at bench {bench} sweeps {sweep:.2f} rad > "
                f"{MAX_SWITCHBACK_SWEEP_RAD} (pit too tight for this grade)")
        for j in range(1, n_samples):
            t = j / (n_samples - 1)
            depth = (bench + t) * design.bench_height_m
            pts.append(_wall_point(design.shape, theta + direction * sweep * t, depth,
                                   design.step_in_m, design.bench_height_m))
        theta += direction * sweep
        direction = -direction
        crossings.append(len(pts) - 1)      # the turn: this leg's end = next leg's start
    return pts, crossings


# ---------------------------------------------------------------- the builder

class _IdAlloc:
    def __init__(self, base: int) -> None:
        self._next = base

    def take(self) -> int:
        v = self._next
        self._next += 1
        return v


def _shorter_arc(theta_from: float, theta_to: float) -> tuple[float, float]:
    """(start, signed sweep) for the shorter angular path theta_from -> theta_to."""
    d = (theta_to - theta_from + math.pi) % (2 * math.pi) - math.pi
    return theta_from, d


def build_open_pit(design: OpenPitDesign) -> OpenPitGeometry:  # noqa: PLR0912, PLR0915
    """Build the pit solid + constrained RoadNetwork from a design. Pure and deterministic."""
    geo = OpenPitGeometry(design=design)
    geo.rings = _build_rings(design)
    th_grid = np.linspace(0.0, 2 * math.pi, N_RING_PTS, endpoint=False)
    geo.floor_r_min_m = float(np.min(
        _ring_radius(design.shape, th_grid, design.n_benches * design.step_in_m)))

    net = RoadNetwork()
    seg_ids = _IdAlloc(1)
    node_ids = _IdAlloc(INFRA_ID_BASE)
    zone_ids = _IdAlloc(1)

    # ---- ramps -> crossing nodes per bench + piecewise-constant-grade segments
    if design.ramp_style == "spiral":
        ramp_paths = [(_spiral_path(design, design.entry_azimuth_rad, design.spiral_ccw), None)]
    elif design.ramp_style == "switchback":
        ramp_paths = [(_switchback_path(design), None)]
    else:
        ramp_paths = [
            (_spiral_path(design, design.entry_azimuth_rad, design.spiral_ccw), "down"),
            (_spiral_path(design, design.entry_azimuth2_rad, not design.spiral_ccw), "up"),
        ]

    one_way_pair = design.ramp_style == "dual_spiral" and design.ramp_lanes == 1
    zoned = design.ramp_style in ("spiral", "switchback") and design.ramp_lanes == 1
    ramp_speed = SWITCHBACK_SPEED_KMH if design.ramp_style == "switchback" else RAMP_SPEED_KMH

    # crossing node ids per ramp: crossing_nodes[r][bench] -> node id
    crossing_nodes: list[list[int]] = []
    for (pts, crossings), role in ramp_paths:
        pts_arr = [np.asarray(p) for p in pts]
        geo.ramp_polylines.append(np.stack(pts_arr, axis=0))
        nodes_this: list[int] = []
        for bench, pi in enumerate(crossings):
            nid = node_ids.take()
            kind = "portal" if bench == 0 else "waypoint"
            p = pts_arr[pi]
            net.add_node(NodeSite(nid, kind, (float(p[0]), float(p[1]), float(p[2]))))
            nodes_this.append(nid)
            if bench == 0:
                geo.portal_ids.append(nid)
        crossing_nodes.append(nodes_this)
        for bench in range(design.n_benches):
            span = pts_arr[crossings[bench]:crossings[bench + 1] + 1]
            poly = np.stack(span, axis=0)
            length = polyline_length(poly)
            upper, lower = nodes_this[bench], nodes_this[bench + 1]
            if role == "up":
                # climb-only ramp: orient lower -> upper, one-way, positive grade
                seg = Segment(id=seg_ids.take(), a=lower, b=upper, polyline=poly[::-1].copy(),
                              length_m=length, grade_pct=design.ramp_grade_pct, width_class=2,
                              one_way=one_way_pair, speed_limit_kmh=ramp_speed,
                              rolling_resistance_pct=IN_PIT_RR_PCT)
            else:
                seg = Segment(id=seg_ids.take(), a=upper, b=lower, polyline=poly,
                              length_m=length, grade_pct=-design.ramp_grade_pct, width_class=2,
                              one_way=one_way_pair and role == "down",
                              speed_limit_kmh=ramp_speed,
                              zone_id=zone_ids.take() if zoned else None,
                              rolling_resistance_pct=IN_PIT_RR_PCT,
                              single_lane_op=zoned)
            net.add_segment(seg)
            if seg.zone_id is not None:
                geo.zones[seg.zone_id] = DirectionZone(
                    id=seg.zone_id, segment_ids=(seg.id,), policy=design.zone_policy,
                    max_in_zone=segment_capacity(length))
        if design.ramp_style == "switchback":
            # every interior 180-degree turn is a capacity-1 conflict point
            for bench in range(1, design.n_benches):
                geo.junctions[nodes_this[bench]] = Junction(
                    id=nodes_this[bench], capacity=1, cross_s=TURN_CROSS_S)

    # ---- faces on benches, tied along the bench arc to EVERY ramp's crossing on that bench.
    # (With a one-way dual_spiral circulation, a face reached from the descent ramp MUST have a
    # bench-road connection to the climb ramp — one arc per ramp is the physical berm road.)
    for face_idx, (bench, az) in enumerate(design.faces):
        face_id = FACE_ID_BASE + face_idx
        inset = bench * design.step_in_m
        cross_info = []
        for nodes_this in crossing_nodes:
            cn = net.nodes[nodes_this[bench]]
            cross_az = math.atan2(cn.pos[1], cn.pos[0])
            _, sweep = _shorter_arc(cross_az, az)
            cross_info.append((nodes_this[bench], cross_az, sweep))
        # keep a usable arc to the NEAREST crossing: nudge the face along the bench if needed
        nearest = min(cross_info, key=lambda ci: abs(ci[2]))
        r_face = float(_ring_radius(design.shape, az, inset))
        while abs(_shorter_arc(nearest[1], az)[1]) * r_face < MIN_BENCH_ARC_M:
            az += 0.2
            r_face = float(_ring_radius(design.shape, az, inset))
        z = -(bench * design.bench_height_m)
        pos = (r_face * math.cos(az), r_face * math.sin(az), z)
        net.add_node(NodeSite(face_id, "face", pos))
        geo.face_nodes[face_id] = (bench, az)
        geo.shovel_bench[face_id] = bench
        for cross_nid, cross_az, _ in cross_info:
            _, sweep = _shorter_arc(cross_az, az)
            n_arc = max(2, int(abs(sweep) / 0.05))
            ths = cross_az + np.linspace(0.0, sweep, n_arc + 1)
            rr = _ring_radius(design.shape, ths, inset)
            arc = np.stack([rr * np.cos(ths), rr * np.sin(ths), np.full_like(ths, z)], axis=1)
            net.add_segment(Segment(
                id=seg_ids.take(), a=cross_nid, b=face_id, polyline=arc,
                length_m=max(polyline_length(arc), 1.0), grade_pct=0.0, width_class=2,
                one_way=False, speed_limit_kmh=BENCH_SPEED_KMH,
                rolling_resistance_pct=IN_PIT_RR_PCT))

    # ---- ex-pit destinations + shared surface trunk
    dest_alloc = _IdAlloc(DEST_ID_BASE)
    dest_positions: list[tuple[int, tuple[float, float, float]]] = []
    order = sorted(range(len(design.destinations)),
                   key=lambda i: {"crusher": 0, "dump": 1, "stockpile": 2}[design.destinations[i][0]])
    for i in order:
        kind, az, dist = design.destinations[i]
        r = float(design.shape.radius(az)) + dist
        nid = dest_alloc.take()
        pos = (r * math.cos(az), r * math.sin(az), 0.0)
        net.add_node(NodeSite(nid, "crusher" if kind == "crusher" else "dump", pos))
        dest_positions.append((nid, pos))
        if kind == "crusher":
            geo.crusher_ids.append(nid)
        elif kind == "dump":
            geo.dump_ids.append(nid)
        else:
            geo.stockpile_id = nid
    if not dest_positions:
        raise PitGeometryError("a pit needs at least one ex-pit destination")

    # junction chain from the primary portal toward the destination centroid
    portal_pos = np.asarray(net.nodes[geo.portal_ids[0]].pos)
    centroid = np.mean([np.asarray(p) for _, p in dest_positions], axis=0)
    if float(np.linalg.norm(centroid[:2] - portal_pos[:2])) < 50.0:
        centroid = portal_pos + np.array([300.0, 0.0, 0.0])
    junction_nids: list[int] = []
    n_j = max(1, design.n_surface_junctions)
    for k in range(1, n_j + 1):
        f = k / (n_j + 1)
        p = portal_pos + f * (centroid - portal_pos)
        nid = node_ids.take()
        net.add_node(NodeSite(nid, "junction", (float(p[0]), float(p[1]), 0.0)))
        geo.junctions[nid] = Junction(id=nid, capacity=2, cross_s=SURFACE_JUNCTION_CROSS_S)
        junction_nids.append(nid)

    def _surface_seg(a: int, b: int) -> None:
        pa, pb = np.asarray(net.nodes[a].pos), np.asarray(net.nodes[b].pos)
        poly = np.stack([pa, pb], axis=0)
        length = max(polyline_length(poly), 1.0)
        net.add_segment(Segment(id=seg_ids.take(), a=a, b=b, polyline=poly, length_m=length,
                                grade_pct=0.0, width_class=2, one_way=False,
                                speed_limit_kmh=SURFACE_SPEED_KMH,
                                rolling_resistance_pct=SURFACE_RR_PCT))

    for portal in geo.portal_ids:
        _surface_seg(portal, junction_nids[0])
    for a, b in zip(junction_nids, junction_nids[1:]):
        _surface_seg(a, b)
    for nid, pos in dest_positions:
        jn = min(junction_nids,
                 key=lambda j: float(np.linalg.norm(
                     np.asarray(net.nodes[j].pos[:2]) - np.asarray(pos[:2]))))
        _surface_seg(jn, nid)

    geo.network = net.freeze()
    issues = geo.network.validate()
    if issues:
        raise PitGeometryError(f"built network fails structural validation: {issues}")
    return geo
