"""The constrained road network: a directed multigraph of Segments between NodeSites.

This is the package's central claim vs prior art: haul routes are NOT a scalar distance matrix —
they are a graph whose edges carry the physical and OPERATIONAL constraints real mines run under:

    - `one_way`        traversal only a->b (spiral ramps are commonly one-way per bench design)
    - `width_class`    1 = single-vehicle lane, 2 = two lanes; a vehicle with a larger width class
                       than the segment cannot use it AT ALL (routing filters it)
    - `zone_id`        membership in a DirectionZone: a chain of single-lane BIDIRECTIONAL
                       segments (an underground drift between passing bays, a narrow pit ramp)
                       where opposing traffic must be arbitrated at runtime
    - `single_lane_op` OPERATIONALLY one lane despite width_class 2: a pit ramp wide enough for
                       the largest truck but built as a single travel lane, so it can join a
                       DirectionZone without excluding wide vehicles (U8 open-pit generator)
    - `grade_pct`      signed in the a->b direction; long ramps are split into piecewise-constant
                       grade segments AT GENERATION TIME so traversal time is closed-form
    - `speed_limit_kmh` curve/junction-approach limits
    - `rolling_resistance_pct` surface quality (2.0 maintained, 2.5 in-pit, 3.0 underground)

Design: plain dicts + numpy polylines (no networkx). Immutable after build (`freeze()`), so route
caches and speed tables can trust it; runtime mutability (closures) is handled by the DES layer
passing a `closed_segments` set into routing, never by mutating the graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import numpy as np


@dataclass(frozen=True)
class NodeSite:
    id: int
    kind: str                              # face|dump|crusher|junction|portal|bay|chute|bin|waypoint
    pos: tuple[float, float, float]        # world metres (x, y, z)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "pos": list(self.pos)}

    @classmethod
    def from_dict(cls, d: dict) -> "NodeSite":
        return cls(int(d["id"]), str(d["kind"]), tuple(float(v) for v in d["pos"]))  # type: ignore[arg-type]


@dataclass(frozen=True)
class Segment:
    id: int
    a: int
    b: int
    polyline: np.ndarray                   # (k, 3) float64 including endpoints
    length_m: float
    grade_pct: float                       # signed, in the a->b direction
    width_class: int                       # 1 single-lane, 2 two-lane
    one_way: bool
    speed_limit_kmh: float
    zone_id: int | None = None
    rolling_resistance_pct: float = 2.0
    single_lane_op: bool = False           # wide enough for the fleet, but ONE travel lane

    def __post_init__(self) -> None:
        if self.length_m <= 0:
            raise ValueError(f"segment {self.id}: length must be > 0")
        if self.width_class not in (1, 2):
            raise ValueError(f"segment {self.id}: width_class must be 1 or 2")
        if self.polyline.ndim != 2 or self.polyline.shape[1] != 3 or self.polyline.shape[0] < 2:
            raise ValueError(f"segment {self.id}: polyline must be (k>=2, 3)")

    def grade_for(self, direction: int) -> float:
        """Signed grade experienced traveling `direction` (+1 = a->b, -1 = b->a)."""
        return self.grade_pct if direction > 0 else -self.grade_pct

    def to_dict(self) -> dict:
        return {
            "id": self.id, "a": self.a, "b": self.b, "polyline": self.polyline.tolist(),
            "length_m": self.length_m, "grade_pct": self.grade_pct, "width_class": self.width_class,
            "one_way": self.one_way, "speed_limit_kmh": self.speed_limit_kmh, "zone_id": self.zone_id,
            "rolling_resistance_pct": self.rolling_resistance_pct,
            "single_lane_op": self.single_lane_op,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        return cls(
            id=int(d["id"]), a=int(d["a"]), b=int(d["b"]),
            polyline=np.asarray(d["polyline"], dtype=np.float64),
            length_m=float(d["length_m"]), grade_pct=float(d["grade_pct"]),
            width_class=int(d["width_class"]), one_way=bool(d["one_way"]),
            speed_limit_kmh=float(d["speed_limit_kmh"]),
            zone_id=None if d.get("zone_id") is None else int(d["zone_id"]),
            rolling_resistance_pct=float(d.get("rolling_resistance_pct", 2.0)),
            single_lane_op=bool(d.get("single_lane_op", False)),
        )


@dataclass
class RoadNetwork:
    """The immutable-after-build network. Use `add_*` then `freeze()`; queries assert frozen."""
    nodes: dict[int, NodeSite] = field(default_factory=dict)
    segments: dict[int, Segment] = field(default_factory=dict)
    out_adj: dict[int, list[tuple[int, int]]] = field(default_factory=dict)  # node -> [(segment_id, direction)]
    _frozen: bool = False

    def add_node(self, node: NodeSite) -> None:
        self._assert_mutable()
        if node.id in self.nodes:
            raise ValueError(f"duplicate node id {node.id}")
        self.nodes[node.id] = node
        self.out_adj.setdefault(node.id, [])

    def add_segment(self, seg: Segment) -> None:
        self._assert_mutable()
        if seg.id in self.segments:
            raise ValueError(f"duplicate segment id {seg.id}")
        if seg.a not in self.nodes or seg.b not in self.nodes:
            raise ValueError(f"segment {seg.id}: endpoints must exist ({seg.a}->{seg.b})")
        self.segments[seg.id] = seg
        self.out_adj[seg.a].append((seg.id, +1))
        if not seg.one_way:
            self.out_adj[seg.b].append((seg.id, -1))

    def freeze(self) -> "RoadNetwork":
        # deterministic adjacency order (by segment id, then direction) for reproducible routing ties
        for nid in self.out_adj:
            self.out_adj[nid].sort()
        self._frozen = True
        return self

    def _assert_mutable(self) -> None:
        if self._frozen:
            raise RuntimeError("network is frozen; build a new one instead of mutating")

    # ---- queries ----
    def leaving(self, node_id: int) -> Iterator[tuple[Segment, int]]:
        """(segment, direction) pairs usable when LEAVING node_id (direction honors one_way)."""
        for sid, direction in self.out_adj.get(node_id, []):
            yield self.segments[sid], direction

    def other_end(self, seg: Segment, direction: int) -> int:
        return seg.b if direction > 0 else seg.a

    def nodes_of_kind(self, kind: str) -> list[NodeSite]:
        return sorted((n for n in self.nodes.values() if n.kind == kind), key=lambda n: n.id)

    def validate(self) -> list[str]:
        """Structural problems (empty = valid): dangling refs, isolated sites, zone width sanity."""
        issues: list[str] = []
        used: set[int] = set()
        for s in self.segments.values():
            used.add(s.a)
            used.add(s.b)
            if s.zone_id is not None and s.width_class != 1 and not s.single_lane_op:
                issues.append(f"segment {s.id}: in DirectionZone {s.zone_id} but width_class=2 "
                              "and not single_lane_op")
        for n in self.nodes.values():
            if n.kind in ("face", "dump", "crusher", "portal", "chute", "bin") and n.id not in used:
                issues.append(f"{n.kind} node {n.id} is not connected to any segment")
        return issues

    def to_dict(self) -> dict:
        return {"nodes": [n.to_dict() for n in self.nodes.values()],
                "segments": [s.to_dict() for s in self.segments.values()]}

    @classmethod
    def from_dict(cls, d: dict) -> "RoadNetwork":
        net = cls()
        for nd in d["nodes"]:
            net.add_node(NodeSite.from_dict(nd))
        for sd in d["segments"]:
            net.add_segment(Segment.from_dict(sd))
        return net.freeze()
