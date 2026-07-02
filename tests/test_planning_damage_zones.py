"""U-P3 acceptance: zone composition by MIN, damage resolution per severity, end-to-end
damage -> closure -> REROUTE, recompute-on-clear with overlapping damages, snapshot round-trip."""
import numpy as np
import pytest

from minehaulsim.equipment import TRUCKS
from minehaulsim.network.graph import NodeSite, RoadNetwork, Segment
from minehaulsim.network.routing import Router
from minehaulsim.planning.damage import (DamageSeverity, SlopeDamageEvent,
                                         resolve_damage)
from minehaulsim.planning.phase import MinePlan, Period
from minehaulsim.planning.state import PitState
from minehaulsim.planning.zones import SpeedZone, ZoneReason, compose_speed_caps
from tests.test_planning_model import hand_model

PLAN = MinePlan("pl", (Period(0, 3600.0, (1,)), Period(1, 3600.0, (1, 2))))


def test_zone_validation_and_min_composition():
    with pytest.raises(ValueError, match="cap must be > 0"):
        SpeedZone(1, "bad", (10,), 0.0)
    with pytest.raises(ValueError, match="empty"):
        SpeedZone(1, "bad", (), 20.0)
    z1 = SpeedZone(1, "infra", (10, 11), 30.0, ZoneReason.INFRASTRUCTURE)
    z2 = SpeedZone(2, "dust", (11, 12), 20.0, ZoneReason.DUST_VISIBILITY)
    caps = compose_speed_caps([z1, z2], extra={12: 15.0, 13: 40.0})
    assert caps == {10: 30.0, 11: 20.0, 12: 15.0, 13: 40.0}   # MIN wins everywhere
    assert SpeedZone.from_dict(z1.to_dict()) == z1


def _wall_net() -> RoadNetwork:
    """Bench-1 floor road under the wall span (z=-15), a deep road (z=-30), a surface road (z=0)."""
    net = RoadNetwork()
    nodes = [(101, "junction", (0.0, 0.0, -15.0)), (2, "junction", (60.0, 0.0, -15.0)),
             (3, "junction", (60.0, 5.0, -30.0)), (4, "junction", (0.0, 5.0, -30.0)),
             (5, "junction", (0.0, 0.0, 0.0)), (6, "junction", (60.0, 0.0, 0.0)),
             (102, "junction", (200.0, 300.0, -15.0)), (103, "junction", (0.0, 400.0, -15.0)),
             (200, "crusher", (300.0, 300.0, 0.0))]
    for nid, kind, pos in nodes:
        net.add_node(NodeSite(nid, kind, pos))
    def seg(sid, a, b, za, zb, y=0.0):
        poly = np.array([[net.nodes[a].pos[0], y, za], [net.nodes[b].pos[0], y, zb]])
        L = float(np.linalg.norm(poly[1] - poly[0]))
        return Segment(id=sid, a=a, b=b, polyline=poly, length_m=max(L, 1.0), grade_pct=0.0,
                       width_class=2, one_way=False, speed_limit_kmh=50.0)
    net.add_node(NodeSite(104, "junction", (0.0, 150.0, -15.0)))   # route origin, OUTSIDE the wall prism
    net.add_segment(seg(1, 101, 2, -15.0, -15.0))          # bench-1 floor road, under the span
    net.add_segment(seg(2, 4, 3, -30.0, -30.0, y=5.0))     # one bench below (runout depth 1)
    net.add_segment(seg(3, 5, 6, 0.0, 0.0))                # surface road above the crest
    # far detour + crusher access (outside any damage geometry)
    far = np.array([[200.0, 300.0, -15.0], [300.0, 300.0, 0.0]])
    net.add_segment(Segment(id=4, a=102, b=200, polyline=far, length_m=320.0, grade_pct=4.7,
                            width_class=2, one_way=False, speed_limit_kmh=50.0))
    far2 = np.array([[0.0, 400.0, -15.0], [200.0, 300.0, -15.0]])
    net.add_segment(Segment(id=5, a=103, b=102, polyline=far2, length_m=224.0, grade_pct=0.0,
                            width_class=2, one_way=False, speed_limit_kmh=50.0))
    # SHORT path 104 -> under-wall junction 2 -> crusher access (damage closes both legs)
    lnk8 = np.array([[0.0, 150.0, -15.0], [60.0, 0.0, -15.0]])
    net.add_segment(Segment(id=8, a=104, b=2, polyline=lnk8, length_m=162.0, grade_pct=0.0,
                            width_class=2, one_way=False, speed_limit_kmh=50.0))
    lnk = np.array([[60.0, 0.0, -15.0], [200.0, 300.0, -15.0]])
    net.add_segment(Segment(id=6, a=2, b=102, polyline=lnk, length_m=331.0, grade_pct=0.0,
                            width_class=2, one_way=False, speed_limit_kmh=50.0))
    # LONG alternate 104 -> 103 -> 102 (stays >= 150 m from the wall span; survives closures)
    alt = np.array([[0.0, 150.0, -15.0], [0.0, 400.0, -15.0]])
    net.add_segment(Segment(id=7, a=104, b=103, polyline=alt, length_m=500.0, grade_pct=0.0,
                            width_class=2, one_way=False, speed_limit_kmh=50.0))
    return net.freeze()


def _event(sev, depth=1):
    # wall span over bench 1 arcs [0, 60] (its polyline runs (0,0,-15)->(100,0,-15))
    return SlopeDamageEvent(id=1, bench_id=1, arc_s0=0.0, arc_s1=60.0, severity=sev,
                            depth_benches=depth)


def test_severity_ladder_resolution():
    m, net = hand_model(), _wall_net()
    # TENSION_CRACKS: footprint derated to 30, nothing closed
    eff = resolve_damage(m, net, _event(DamageSeverity.TENSION_CRACKS))
    assert not eff.closed_segments
    assert dict(eff.derated).get(1) == 30.0                # bench-floor road derated
    assert eff.exclusion_zone and eff.exclusion_zone.reason == ZoneReason.SLOPE_DAMAGE
    # WEDGE_FAILURE: footprint CLOSED, buffer derated to 20
    eff2 = resolve_damage(m, net, _event(DamageSeverity.WEDGE_FAILURE))
    assert 1 in eff2.closed_segments and 2 in eff2.closed_segments   # floor + runout road
    assert 3 not in eff2.closed_segments                             # surface above crest: buffer only
    assert dict(eff2.derated).get(3) == 20.0
    # WALL_COLLAPSE: footprint AND buffer closed
    eff3 = resolve_damage(m, net, _event(DamageSeverity.WALL_COLLAPSE))
    assert {1, 2, 3} <= set(eff3.closed_segments)
    assert not eff3.derated
    # depth 0 keeps the lower bench out of the footprint
    eff4 = resolve_damage(m, net, _event(DamageSeverity.WEDGE_FAILURE, depth=0))
    assert 2 not in eff4.closed_segments


def test_damage_closure_forces_reroute_end_to_end():
    m, net = hand_model(), _wall_net()
    st = PitState(m, PLAN)
    router = Router(net)
    truck = TRUCKS["CAT_793F"]
    closed0, caps0 = st.routing_inputs()
    base = router.route(104, 200, truck, loaded=True, closed=closed0, speed_caps=caps0)
    assert [u.segment_id for u in base.uses] == [8, 6, 4]           # the short way under the wall
    st.apply_damage(_event(DamageSeverity.WEDGE_FAILURE), net)
    closed1, caps1 = st.routing_inputs()
    assert {8, 6} <= closed1                                        # both under-wall legs closed
    rerouted = router.route(104, 200, truck, loaded=True, closed=closed1, speed_caps=caps1)
    assert rerouted is not None
    assert [u.segment_id for u in rerouted.uses] == [7, 5, 4]       # around the exclusion, via 103
    assert rerouted.time_s > base.time_s
    # and the face anchored UNDER the wall (101) becomes unreachable — the honest severed case
    assert router.route(104, 101, truck, loaded=False, closed=closed1, speed_caps=caps1) is None


def test_overlapping_damages_recompute_on_clear():
    m, net = hand_model(), _wall_net()
    st = PitState(m, PLAN)
    e1 = _event(DamageSeverity.WEDGE_FAILURE)
    e2 = SlopeDamageEvent(id=2, bench_id=1, arc_s0=30.0, arc_s1=90.0,
                          severity=DamageSeverity.TENSION_CRACKS)
    st.apply_damage(e1, net)
    st.apply_damage(e2, net)
    closed, caps = st.routing_inputs()
    assert 1 in closed                                   # closure dominates the overlapping deration
    assert 1 not in caps
    st.clear_damage(1)                                   # e1 gone; e2's deration must SURVIVE
    closed2, caps2 = st.routing_inputs()
    assert 1 not in closed2
    assert caps2.get(1) == 30.0                          # recomputed from the active set, not decremented
    with pytest.raises(KeyError):
        st.clear_damage(99)
    with pytest.raises(ValueError):
        st.apply_damage(e2, net)                         # duplicate id rejected


def test_zone_lifecycle_and_snapshot_round_trip():
    m, net = hand_model(), _wall_net()
    st = PitState(m, PLAN)
    st.add_zone(SpeedZone(10, "crusher-approach", (4,), 25.0, ZoneReason.INFRASTRUCTURE))
    st.apply_damage(_event(DamageSeverity.TENSION_CRACKS), net)
    closed, caps = st.routing_inputs()
    assert caps.get(4) == 25.0 and caps.get(1) == 30.0
    snap = st.to_dict()
    st2 = PitState.from_dict(hand_model(), PLAN, snap)
    assert st2.routing_inputs() == st.routing_inputs()
    assert len(st2.active_damages()) == 1
    st2.clear_damage(1)
    st2.remove_zone(10)
    assert st2.routing_inputs() == (frozenset(st2.overlay().retired_segments), {})
    with pytest.raises(KeyError):
        st2.remove_zone(10)