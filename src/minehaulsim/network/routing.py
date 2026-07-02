"""Constrained shortest-expected-time routing over the RoadNetwork.

Admissibility (the constraints made operational):
    - width: a unit with width_class > segment.width_class cannot use the segment at all
    - direction: one_way segments only a->b (the graph's adjacency already encodes this)
    - closures: a `closed` set of segment ids (breakdowns, slope-damage closures) excludes edges
      WITHOUT mutating the frozen graph — the DES/planning layers own that state
    - speed caps: an optional `speed_caps` mapping (segment id -> cap km/h) composes by MIN with
      the segment's own limit (speed-restricted zones feed this)

Cost of an edge = free-flow kinematic traversal time for (unit, loaded GVW, signed grade, rolling
resistance, MIN(limit, cap)) + expected junction cross time at the entry node + a configurable
per-zone congestion prior. Deterministic: ties break on (cost, segment id); heap entries carry a
monotone sequence number so equal-cost pops are FIFO across platforms.

Route cache: keyed by (origin, dest, unit, loaded, closures-key, caps-key); the caller passes the
same frozen inputs, cache hits are O(1). Invalidation is automatic because the key CONTAINS the
closure/cap state (frozensets) — a closure change is simply a different key.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass

from ..equipment.catalog import LhdClass, TruckClass
from .constraints import Junction
from .graph import RoadNetwork, Segment
from .kinematics import SpeedSolver, traverse_time_s


@dataclass(frozen=True)
class SegmentUse:
    segment_id: int
    direction: int          # +1 a->b, -1 b->a


@dataclass(frozen=True)
class Route:
    origin: int
    dest: int
    uses: tuple[SegmentUse, ...]
    time_s: float           # expected free-flow time (the routing cost)
    length_m: float

    @property
    def empty(self) -> bool:
        return not self.uses


class Router:
    def __init__(self, net: RoadNetwork, junctions: dict[int, Junction] | None = None,
                 zone_penalty_s: float = 0.0) -> None:
        self.net = net
        self.junctions = junctions or {}
        self.zone_penalty_s = zone_penalty_s
        self._speed = SpeedSolver()
        self._cache: dict[tuple, Route | None] = {}

    def _edge_cost(self, seg: Segment, direction: int, unit: TruckClass | LhdClass,
                   gvw_t: float, loaded: bool, cap_kmh: float | None) -> float | None:
        """Free-flow traversal cost, or None if the edge is inadmissible/stalling."""
        if unit.width_class > seg.width_class:
            return None
        limit = seg.speed_limit_kmh if cap_kmh is None else min(seg.speed_limit_kmh, cap_kmh)
        v = self._speed.speed_ms(unit, gvw_t, seg.grade_for(direction),
                                 seg.rolling_resistance_pct, limit)
        if v <= 0:
            return None                      # the unit stalls on this grade: not a usable edge
        t = traverse_time_s(seg.length_m, v, v, loaded)   # free-flow: enter at segment speed
        if seg.zone_id is not None:
            t += self.zone_penalty_s
        return t

    def route(self, origin: int, dest: int, unit: TruckClass | LhdClass, loaded: bool,
              gvw_t: float | None = None,
              closed: frozenset[int] = frozenset(),
              speed_caps: dict[int, float] | None = None) -> Route | None:
        """Constrained shortest-expected-time route, or None if unreachable."""
        if origin not in self.net.nodes or dest not in self.net.nodes:
            raise ValueError(f"unknown node in route request: {origin}->{dest}")
        gvw = gvw_t if gvw_t is not None else (
            unit.empty_t + (getattr(unit, "payload_mean_t", getattr(unit, "bucket_t", 0.0)) if loaded else 0.0))
        caps = speed_caps or {}
        caps_key = frozenset(caps.items())
        key = (origin, dest, unit.name, loaded, SpeedSolver.gvw_bucket(unit, gvw), closed, caps_key)
        if key in self._cache:
            return self._cache[key]

        # Dijkstra: (cost, seq, node); parent map stores the chosen (segment, direction)
        best: dict[int, float] = {origin: 0.0}
        parent: dict[int, tuple[int, int]] = {}
        seq = 0
        heap: list[tuple[float, int, int]] = [(0.0, seq, origin)]
        visited: set[int] = set()
        while heap:
            cost, _, node = heapq.heappop(heap)
            if node in visited:
                continue
            visited.add(node)
            if node == dest:
                break
            jx = self.junctions.get(node)
            j_cost = jx.cross_s if (jx and node != origin) else 0.0
            for seg, direction in self.net.leaving(node):
                if seg.id in closed:
                    continue
                ec = self._edge_cost(seg, direction, unit, gvw, loaded, caps.get(seg.id))
                if ec is None:
                    continue
                nxt = self.net.other_end(seg, direction)
                nc = cost + j_cost + ec
                if nc < best.get(nxt, float("inf")) - 1e-12:
                    best[nxt] = nc
                    parent[nxt] = (seg.id, direction)
                    seq += 1
                    heapq.heappush(heap, (nc, seq, nxt))

        if dest not in visited and dest not in best:
            self._cache[key] = None
            return None
        # reconstruct
        uses: list[SegmentUse] = []
        node = dest
        length = 0.0
        while node != origin:
            sid, direction = parent[node]
            seg = self.net.segments[sid]
            uses.append(SegmentUse(sid, direction))
            length += seg.length_m
            node = seg.a if direction > 0 else seg.b
        uses.reverse()
        route = Route(origin=origin, dest=dest, uses=tuple(uses),
                      time_s=best[dest], length_m=length)
        self._cache[key] = route
        return route
