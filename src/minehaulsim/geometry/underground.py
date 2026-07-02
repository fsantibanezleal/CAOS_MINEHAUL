"""Multi-level underground mine geometry: graph-first generation (PLUME-style skeleton with
DOT-style decline constraints).

Structure built by `build_underground(design)` (pure, RNG-free — the scenario generator samples
the design):

- **Levels**: `n_levels` horizontal working levels, first at `-first_level_depth_m`, spaced
  `level_spacing_m`. The HAULAGE level is the lowest.
- **Decline**: the only driveable access from surface. Two styles: `spiral` (helix of radius
  `spiral_radius_m`, 25..40 m) or `zigzag` (straight legs with 180-degree switchback turns,
  each turn a capacity-1 Junction). Grade fixed by design (1:8..1:6.5 = 12.5..15.4%),
  single-lane (`width_class=1`). **Passing bays** are inserted every `passing_bay_spacing_m`
  of decline length: each bay is a node splitting the decline; every inter-bay span is ONE
  DirectionZone (opposing traffic arbitrates at the bays — the underground reality that makes
  decline traffic THE bottleneck).
- **Per level**: an access node where the decline crosses the level elevation; `n_drifts`
  dead-end production drifts (each a capacity-1 single-vehicle DirectionZone) ending in 1..3
  drawpoints (faces); a connection drift from the access node to the level's ore-pass tip
  (when an ore pass spans the level).
- **Ore passes**: NOT driveable. Each is a vertical material teleport with finite capacity:
  tip nodes on its spanned upper levels, one chute node on the haulage level. The DES couples
  LHD tips to chute loading through the inventory (des/sim.py).
- **Shaft** (optional): a bin node on the haulage level; trucks may dump there (hoisting drains
  the bin at a rate — abstracted in the DES).
- **Surface**: portal at the decline top + a crusher/dump destination a short trunk away.

Node id blocks: drawpoints + chutes (the cyclelog "shovels") take 1..N; surface/bin dumps 101..;
infrastructure (portal, access, bays, tips, stubs, junctions) from 1000.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..network.constraints import DirectionZone, Junction, ZonePolicy
from ..network.graph import NodeSite, RoadNetwork, Segment
from .paths import polyline_length

DECLINE_SPEED_KMH = 25.0
DRIFT_SPEED_KMH = 15.0
SURFACE_SPEED_KMH = 40.0
UG_RR_PCT = 3.0
SURFACE_RR_PCT = 2.0
TURN_CROSS_S = 12.0
CHUTE_ID_KIND = "chute"

DEST_ID_BASE = 101
INFRA_ID_BASE = 1_000


class UndergroundGeometryError(ValueError):
    """Unbuildable underground design; scenario generators resample on this."""


@dataclass(frozen=True)
class DriftSpec:
    length_m: float
    n_drawpoints: int
    azimuth_rad: float


@dataclass(frozen=True)
class LevelSpec:
    drifts: tuple[DriftSpec, ...]


@dataclass(frozen=True)
class OrePassSpec:
    top_level: int                    # inclusive level index (0 = shallowest)
    bottom_level: int                 # inclusive; tips exist on levels [top..bottom]
    capacity_t: float
    azimuth_rad: float                # where on each level the tip sits (radius from access)
    tip_offset_m: float = 60.0


@dataclass(frozen=True)
class UndergroundDesign:
    n_levels: int
    first_level_depth_m: float        # 60..120
    level_spacing_m: float            # 25..60
    decline_style: str                # spiral | zigzag
    decline_grade_pct: float          # 12.5..15.4  (1:8 .. 1:6.5)
    spiral_radius_m: float = 32.0
    passing_bay_spacing_m: float = 250.0
    zone_policy: ZonePolicy = ZonePolicy.LOCKOUT
    levels: tuple[LevelSpec, ...] = ()
    ore_passes: tuple[OrePassSpec, ...] = ()
    shaft: bool = False
    shaft_bin_capacity_t: float = 400.0
    shaft_hoist_tph: float = 600.0
    flow_mode: str = "lhd_orepass_truck"    # | truck_direct | truck_shaft
    surface_dump_dist_m: float = 250.0

    def __post_init__(self) -> None:
        if self.decline_style not in ("spiral", "zigzag"):
            raise UndergroundGeometryError(f"unknown decline_style {self.decline_style!r}")
        if self.flow_mode not in ("lhd_orepass_truck", "truck_direct", "truck_shaft"):
            raise UndergroundGeometryError(f"unknown flow_mode {self.flow_mode!r}")
        if not (10.0 <= self.decline_grade_pct <= 16.0):
            raise UndergroundGeometryError("decline grade must be in [10, 16]% (1:10..1:6.25)")
        if len(self.levels) != self.n_levels:
            raise UndergroundGeometryError("levels tuple must match n_levels")
        if self.flow_mode == "truck_shaft" and not self.shaft:
            raise UndergroundGeometryError("truck_shaft flow needs shaft=True")
        if self.flow_mode in ("lhd_orepass_truck", "truck_shaft") and not self.ore_passes:
            raise UndergroundGeometryError(f"{self.flow_mode} needs at least one ore pass")
        for op in self.ore_passes:
            if not (0 <= op.top_level <= op.bottom_level < self.n_levels - 1):
                raise UndergroundGeometryError(
                    "ore pass must span upper levels only (above the haulage level)")

    def level_z(self, i: int) -> float:
        return -(self.first_level_depth_m + i * self.level_spacing_m)

    @property
    def haulage_level(self) -> int:
        return self.n_levels - 1


@dataclass
class UndergroundGeometry:
    design: UndergroundDesign
    network: RoadNetwork | None = None
    zones: dict[int, DirectionZone] = field(default_factory=dict)
    junctions: dict[int, Junction] = field(default_factory=dict)
    portal_id: int = 0
    access_nodes: dict[int, int] = field(default_factory=dict)      # level -> node id
    drawpoints: dict[int, list[int]] = field(default_factory=dict)  # level -> face node ids
    drift_stubs: dict[int, list[int]] = field(default_factory=dict)  # level -> stub node ids
    tips: dict[int, dict[int, int]] = field(default_factory=dict)   # pass idx -> level -> tip id
    chutes: dict[int, int] = field(default_factory=dict)            # pass idx -> chute node id
    bin_id: int | None = None
    surface_dump_id: int = 0
    decline_polyline: np.ndarray | None = None
    level_planes: dict[int, float] = field(default_factory=dict)    # level -> z

    def minetopo_payload(self) -> dict:
        """The minehaulsim.minetopo/v1 pieces (io.write_mine_topo consumes them)."""
        assert self.network is not None and self.decline_polyline is not None
        levels = [{"index": i, "z": z,
                   "drawpoints": [list(self.network.nodes[d].pos) for d in
                                  self.drawpoints.get(i, [])]}
                  for i, z in sorted(self.level_planes.items())]
        shafts = ([{"bin": list(self.network.nodes[self.bin_id].pos)}]
                  if self.bin_id is not None else [])
        passes = [{"chute": list(self.network.nodes[chute].pos),
                   "tips": [list(self.network.nodes[t].pos) for t in
                            sorted(self.tips[pi].values())]}
                  for pi, chute in sorted(self.chutes.items())]
        return {"levels": levels, "decline": self.decline_polyline.tolist(),
                "shafts": shafts, "ore_passes": passes}


class _Ids:
    def __init__(self, base: int) -> None:
        self._n = base

    def take(self) -> int:
        v = self._n
        self._n += 1
        return v


def _decline_path(design: UndergroundDesign) -> tuple[np.ndarray, dict[int, int], list[int]]:
    """Decline polyline from surface to the haulage level. Returns (points, level->point index,
    turn point indices [zigzag only])."""
    grade = design.decline_grade_pct / 100.0
    depth_total = -design.level_z(design.haulage_level)
    pts: list[np.ndarray] = [np.array([0.0, 0.0, 0.0])]
    level_at: dict[int, int] = {}
    turns: list[int] = []
    targets = {(-design.level_z(i)): i for i in range(design.n_levels)}
    depth = 0.0

    if design.decline_style == "spiral":
        r = design.spiral_radius_m
        d_theta = 0.35
        theta = 0.0
        while depth < depth_total - 1e-9:
            step_h = r * d_theta
            d_depth = step_h * grade
            # snap to the next level boundary when the step crosses it
            next_targets = [t for t in targets if depth < t <= depth + d_depth + 1e-9]
            if next_targets:
                t0 = min(next_targets)
                f = (t0 - depth) / d_depth
                theta += f * d_theta
                depth = t0
            else:
                theta += d_theta
                depth += d_depth
            pts.append(np.array([r * math.cos(theta) - r, r * math.sin(theta), -depth]))
            if depth in targets:
                level_at[targets[depth]] = len(pts) - 1
    else:                                   # zigzag: straight legs, 180-degree turns
        leg_h = 120.0                       # horizontal metres per leg
        direction = 1.0
        x, y = 0.0, 0.0
        while depth < depth_total - 1e-9:
            d_depth = leg_h * grade
            remaining = depth_total - depth
            span_h = leg_h if d_depth <= remaining else remaining / grade
            crossings = sorted(t for t in targets if depth < t <= depth + span_h * grade + 1e-9)
            x_start = x
            for t0 in crossings:
                f_h = (t0 - depth) / grade
                pts.append(np.array([x_start + direction * f_h, y, -t0]))
                level_at[targets[t0]] = len(pts) - 1
            x = x_start + direction * span_h
            depth = min(depth + span_h * grade, depth_total)
            if not (crossings and abs(crossings[-1] - depth) < 1e-9):
                pts.append(np.array([x, y, -depth]))
            if depth < depth_total - 1e-9:
                turns.append(len(pts) - 1)
                y += 18.0                   # turn pocket offset
                direction = -direction
    return np.stack(pts, axis=0), level_at, turns


def build_underground(design: UndergroundDesign) -> UndergroundGeometry:  # noqa: PLR0912, PLR0915
    geo = UndergroundGeometry(design=design)
    net = RoadNetwork()
    seg_ids = _Ids(1)
    node_ids = _Ids(INFRA_ID_BASE)
    zone_ids = _Ids(1)
    face_ids = _Ids(1)
    dest_ids = _Ids(DEST_ID_BASE)

    decline, level_at, turns = _decline_path(design)
    geo.decline_polyline = decline
    geo.level_planes = {i: design.level_z(i) for i in range(design.n_levels)}

    # ---- decline nodes: portal, level accesses, turn junctions, passing bays
    geo.portal_id = node_ids.take()
    net.add_node(NodeSite(geo.portal_id, "portal", tuple(decline[0])))
    special: dict[int, int] = {0: geo.portal_id}            # point index -> node id
    for lvl, pi in level_at.items():
        nid = node_ids.take()
        net.add_node(NodeSite(nid, "junction", tuple(decline[pi])))
        geo.access_nodes[lvl] = nid
        special[pi] = nid
    for pi in turns:
        if pi not in special:
            nid = node_ids.take()
            net.add_node(NodeSite(nid, "waypoint", tuple(decline[pi])))
            special[pi] = nid
            geo.junctions[nid] = Junction(id=nid, capacity=1, cross_s=TURN_CROSS_S)

    # passing bays every bay_spacing along the decline arc (skipping points already special)
    seg_lens = np.hypot(np.hypot(np.diff(decline[:, 0]), np.diff(decline[:, 1])),
                        np.diff(decline[:, 2]))
    arc = np.concatenate([[0.0], np.cumsum(seg_lens)])
    next_bay = design.passing_bay_spacing_m
    for pi in range(1, len(decline)):
        if arc[pi] >= next_bay and pi not in special:
            nid = node_ids.take()
            net.add_node(NodeSite(nid, "bay", tuple(decline[pi])))
            special[pi] = nid
            next_bay = arc[pi] + design.passing_bay_spacing_m
        elif pi in special:
            next_bay = max(next_bay, arc[pi] + design.passing_bay_spacing_m)

    # decline segments between consecutive special points; each span = one DirectionZone
    cut_points = sorted(special)
    for a_pi, b_pi in zip(cut_points, cut_points[1:]):
        poly = decline[a_pi:b_pi + 1]
        length = polyline_length(poly)
        zid = zone_ids.take()
        seg = Segment(id=seg_ids.take(), a=special[a_pi], b=special[b_pi], polyline=poly.copy(),
                      length_m=length, grade_pct=-design.decline_grade_pct, width_class=1,
                      one_way=False, speed_limit_kmh=DECLINE_SPEED_KMH, zone_id=zid,
                      rolling_resistance_pct=UG_RR_PCT)
        net.add_segment(seg)
        geo.zones[zid] = DirectionZone(id=zid, segment_ids=(seg.id,), policy=design.zone_policy,
                                       max_in_zone=2)

    # ---- per level: drifts (dead-end capacity-1 zones) with drawpoints.
    # Cyclelog shovel ids (1..N) go to whatever the TRUCK fleet loads at (IO contract 9.1):
    # truck_direct -> the drift stubs (LHD loads trucks there); LHD flows -> the chutes below.
    # Drawpoints are LHD dig targets, never cyclelog shovels — they keep infrastructure ids.
    truck_direct = design.flow_mode == "truck_direct"
    for lvl, spec in enumerate(design.levels):
        access = geo.access_nodes[lvl]
        ax, ay, az = net.nodes[access].pos
        geo.drawpoints[lvl] = []
        geo.drift_stubs[lvl] = []
        for drift in spec.drifts:
            ux, uy = math.cos(drift.azimuth_rad), math.sin(drift.azimuth_rad)
            stub = face_ids.take() if truck_direct else node_ids.take()
            stub_pos = (ax + ux * drift.length_m, ay + uy * drift.length_m, az)
            net.add_node(NodeSite(stub, "face" if truck_direct else "waypoint", stub_pos))
            poly = np.array([[ax, ay, az], list(stub_pos)])
            zid = zone_ids.take()
            seg = Segment(id=seg_ids.take(), a=access, b=stub, polyline=poly,
                          length_m=drift.length_m, grade_pct=0.0, width_class=1, one_way=False,
                          speed_limit_kmh=DRIFT_SPEED_KMH, zone_id=zid,
                          rolling_resistance_pct=UG_RR_PCT)
            net.add_segment(seg)
            geo.zones[zid] = DirectionZone(id=zid, segment_ids=(seg.id,),
                                           policy=ZonePolicy.LOCKOUT, max_in_zone=1)
            geo.drift_stubs[lvl].append(stub)
            for k in range(drift.n_drawpoints):
                fid = node_ids.take()
                dp_pos = (stub_pos[0] + 18.0 * (k + 1) * uy,     # fan out sideways off the stub
                          stub_pos[1] - 18.0 * (k + 1) * ux, az)
                net.add_node(NodeSite(fid, "face", dp_pos))
                dp_poly = np.array([list(stub_pos), list(dp_pos)])
                net.add_segment(Segment(id=seg_ids.take(), a=stub, b=fid, polyline=dp_poly,
                                        length_m=polyline_length(dp_poly), grade_pct=0.0,
                                        width_class=1, one_way=False,
                                        speed_limit_kmh=DRIFT_SPEED_KMH,
                                        rolling_resistance_pct=UG_RR_PCT))
                geo.drawpoints[lvl].append(fid)

    # ---- ore passes: tips on spanned levels (linked by a connection drift), chute on haulage
    for pi, op in enumerate(design.ore_passes):
        geo.tips[pi] = {}
        for lvl in range(op.top_level, op.bottom_level + 1):
            access = geo.access_nodes[lvl]
            ax, ay, az = net.nodes[access].pos
            tip = node_ids.take()
            tip_pos = (ax + op.tip_offset_m * math.cos(op.azimuth_rad),
                       ay + op.tip_offset_m * math.sin(op.azimuth_rad), az)
            net.add_node(NodeSite(tip, "waypoint", tip_pos))
            poly = np.array([[ax, ay, az], list(tip_pos)])
            net.add_segment(Segment(id=seg_ids.take(), a=access, b=tip, polyline=poly,
                                    length_m=polyline_length(poly), grade_pct=0.0, width_class=1,
                                    one_way=False, speed_limit_kmh=DRIFT_SPEED_KMH,
                                    rolling_resistance_pct=UG_RR_PCT))
            geo.tips[pi][lvl] = tip
        # chute: a LOADING point (cyclelog shovel) on the haulage level
        haul = design.haulage_level
        access = geo.access_nodes[haul]
        ax, ay, az = net.nodes[access].pos
        chute = face_ids.take()
        chute_pos = (ax + op.tip_offset_m * math.cos(op.azimuth_rad),
                     ay + op.tip_offset_m * math.sin(op.azimuth_rad), az)
        net.add_node(NodeSite(chute, CHUTE_ID_KIND, chute_pos))
        poly = np.array([[ax, ay, az], list(chute_pos)])
        net.add_segment(Segment(id=seg_ids.take(), a=access, b=chute, polyline=poly,
                                length_m=polyline_length(poly), grade_pct=0.0, width_class=1,
                                one_way=False, speed_limit_kmh=DRIFT_SPEED_KMH,
                                rolling_resistance_pct=UG_RR_PCT))
        geo.chutes[pi] = chute

    # ---- shaft bin (haulage level dump) + surface destination
    if design.shaft:
        haul_access = geo.access_nodes[design.haulage_level]
        ax, ay, az = net.nodes[haul_access].pos
        geo.bin_id = dest_ids.take()
        bin_pos = (ax - 40.0, ay - 40.0, az)
        net.add_node(NodeSite(geo.bin_id, "bin", bin_pos))
        poly = np.array([[ax, ay, az], list(bin_pos)])
        net.add_segment(Segment(id=seg_ids.take(), a=haul_access, b=geo.bin_id, polyline=poly,
                                length_m=polyline_length(poly), grade_pct=0.0, width_class=1,
                                one_way=False, speed_limit_kmh=DRIFT_SPEED_KMH,
                                rolling_resistance_pct=UG_RR_PCT))

    geo.surface_dump_id = dest_ids.take()
    px, py, pz = net.nodes[geo.portal_id].pos
    dump_pos = (px + design.surface_dump_dist_m, py, 0.0)
    net.add_node(NodeSite(geo.surface_dump_id, "dump", dump_pos))
    poly = np.array([[px, py, pz], list(dump_pos)])
    net.add_segment(Segment(id=seg_ids.take(), a=geo.portal_id, b=geo.surface_dump_id,
                            polyline=poly, length_m=polyline_length(poly), grade_pct=0.0,
                            width_class=2, one_way=False, speed_limit_kmh=SURFACE_SPEED_KMH,
                            rolling_resistance_pct=SURFACE_RR_PCT))

    geo.network = net.freeze()
    issues = geo.network.validate()
    if issues:
        raise UndergroundGeometryError(f"built network fails structural validation: {issues}")
    return geo
