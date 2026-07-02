"""Per-segment traversal with TRAFFIC: the tier that makes congestion EMERGE instead of being faked.

A route is traversed leg by leg. Each leg:
    1. JUNCTION at the entry node (if configured): FIFO queue, crossing holds it `cross_s`.
    2. DIRECTION ZONE (seg.zone_id set): request entry for this direction — opposing traffic is
       arbitrated by the zone's policy (lockout / loaded_priority / group_batching, U5 resources).
    3. SEGMENT SLOTS: capacity = max(1, floor(length/headway_m)) per direction; entry blocks when
       full (FIFO wakeup on release).
    4. KINEMATIC time from the SpeedSolver (unit + GVW + signed grade + rolling + MIN(limit, cap)),
       then the FIFO NO-OVERTAKE rule: exit_t = max(own_kinematic_exit, predecessor_exit + headway_s).
       Rule 4 is what serializes fast trucks behind a slow loaded one on a ramp — bunching emerges
       (Soofastaei 2016), it is never sampled from a distribution.

Determinism: all waits resolve through the U5 resources (FIFO by event sequence); no RNG here.
`fast_mode=True` in the sim bypasses this module entirely (free-flow, for quick statistical runs).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from ..equipment.catalog import LhdClass, TruckClass
from ..network.constraints import DirectionZone, Junction, segment_capacity
from ..network.graph import RoadNetwork, Segment
from ..network.kinematics import SpeedSolver, traverse_time_s
from ..network.routing import Route
from .engine import Engine
from .resources import DirectionZoneResource, QueueResource, SlotResource

HEADWAY_S = 8.0


@dataclass
class _SegTraffic:
    slots: SlotResource
    last_exit_t: dict[int, float] = field(default_factory=lambda: {+1: -1e18, -1: -1e18})
    waiting: deque[tuple[int, Callable[[], None]]] = field(default_factory=deque)
    _seq: int = 0


class TrafficState:
    """Per-network runtime traffic state. Keyed by segment id — survives overlay rebuilds for
    unchanged segments; overlay-added segments get fresh state on first use."""

    def __init__(self, engine: Engine, net: RoadNetwork,
                 zones: dict[int, DirectionZone] | None = None,
                 junctions: dict[int, Junction] | None = None,
                 headway_m: float = 80.0, headway_s: float = HEADWAY_S) -> None:
        self.engine = engine
        self.net = net
        self.headway_m = headway_m
        self.headway_s = headway_s
        self.speed = SpeedSolver()
        self._segs: dict[int, _SegTraffic] = {}
        self._zones: dict[int, DirectionZoneResource] = {}
        self._junctions: dict[int, QueueResource] = {}
        self._junction_cross: dict[int, float] = {}
        for zid, zspec in (zones or {}).items():
            self._zones[zid] = DirectionZoneResource(engine=engine, spec=zspec)
        for jid, jspec in (junctions or {}).items():
            self._junctions[jid] = QueueResource(engine=engine, capacity=jspec.capacity)
            self._junction_cross[jid] = jspec.cross_s

    def rebind(self, net: RoadNetwork) -> None:
        """Point at a rebuilt effective network (overlay revision bump); traffic state persists."""
        self.net = net

    def _seg_state(self, seg: Segment) -> _SegTraffic:
        st = self._segs.get(seg.id)
        if st is None:
            st = _SegTraffic(slots=SlotResource(capacity=segment_capacity(seg.length_m, self.headway_m)))
            self._segs[seg.id] = st
        return st

    # ---- the traversal ----
    def traverse(self, unit: TruckClass | LhdClass, gvw_t: float, loaded: bool, route: Route,
                 speed_caps: dict[int, float], on_done: Callable[[], None]) -> None:
        legs = list(route.uses)
        net = self.net          # capture: in-flight legs finish on the geometry they were quoted on
                                # (a rebind mid-route must never strand a truck; design P4)

        def next_leg(i: int, v_entry: float) -> None:
            if i >= len(legs):
                on_done()
                return
            use = legs[i]
            seg = net.segments[use.segment_id]
            entry_node = seg.a if use.direction > 0 else seg.b

            def after_junction() -> None:
                self._enter_zone_then_segment(unit, gvw_t, loaded, seg, use.direction, v_entry,
                                              speed_caps, lambda v_exit: next_leg(i + 1, v_exit))

            jq = self._junctions.get(entry_node)
            if jq is not None and i > 0:                 # no junction hold at the very first node
                def cross() -> None:
                    self.engine.after(self._junction_cross[entry_node], _release_then, jq, after_junction)
                jq.request(cross)
            else:
                after_junction()

        next_leg(0, 0.0)

    def _enter_zone_then_segment(self, unit, gvw_t, loaded, seg: Segment, direction: int,
                                 v_entry: float, caps: dict[int, float],
                                 done: Callable[[float], None]) -> None:
        zres = self._zones.get(seg.zone_id) if seg.zone_id is not None else None

        def enter_segment() -> None:
            st = self._seg_state(seg)

            def occupy() -> None:
                limit = seg.speed_limit_kmh if seg.id not in caps else min(seg.speed_limit_kmh, caps[seg.id])
                v = self.speed.speed_ms(unit, gvw_t, seg.grade_for(direction),
                                        seg.rolling_resistance_pct, limit)
                if v <= 0:
                    raise RuntimeError(f"stall on segment {seg.id} (grade beyond capability)")
                t_kin = traverse_time_s(seg.length_m, v, v_entry, loaded)
                own_exit = self.engine.now + t_kin
                exit_t = max(own_exit, st.last_exit_t[direction] + self.headway_s)  # NO-OVERTAKE
                st.last_exit_t[direction] = exit_t

                def leave() -> None:
                    st.slots.release()
                    if st.waiting:
                        _, wake = st.waiting.popleft()
                        wake()
                    if zres is not None:
                        zres.exit()
                    done(v)

                self.engine.schedule(exit_t, leave)

            if st.slots.try_acquire():
                occupy()
            else:
                st._seq += 1
                st.waiting.append((st._seq, lambda: (st.slots.try_acquire(), occupy())[1]))

        if zres is not None:
            zres.request_entry(direction, loaded, enter_segment)
        else:
            enter_segment()


def _release_then(q: QueueResource, then: Callable[[], None]) -> None:
    q.release()
    then()
