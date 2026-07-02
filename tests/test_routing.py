"""Constrained routing on a hand-built toy network (U4 acceptance).

Topology (flat, rr=2%, limit 40 km/h unless noted):

      1(face) --10(500m)-- 2(junction) --11(400m)-- 3(crusher)
                            \\--12(1200m, wide detour)--/
      segment 13: 2->4(dump), one-way, 300 m
      segment 14: 1->3 direct but width_class=1 (narrow shortcut, 600 m)

A width-2 truck must go 10+11 (short) and CANNOT take 14; closing 11 forces the 12 detour;
a width-1 UG truck may take the 14 shortcut. All expectations hand-checkable.
"""
import numpy as np
import pytest

from minehaulsim.equipment import TRUCKS
from minehaulsim.network.constraints import Junction
from minehaulsim.network.graph import NodeSite, RoadNetwork, Segment
from minehaulsim.network.routing import Router


def _seg(sid, a, b, length, width=2, one_way=False, grade=0.0, limit=40.0):
    poly = np.array([[0.0, 0.0, 0.0], [length, 0.0, length * grade / 100.0]])
    return Segment(id=sid, a=a, b=b, polyline=poly, length_m=length, grade_pct=grade,
                   width_class=width, one_way=one_way, speed_limit_kmh=limit)


@pytest.fixture()
def net():
    n = RoadNetwork()
    for nid, kind in [(1, "face"), (2, "junction"), (3, "crusher"), (4, "dump")]:
        n.add_node(NodeSite(nid, kind, (0.0, 0.0, 0.0)))
    n.add_segment(_seg(10, 1, 2, 500.0))
    n.add_segment(_seg(11, 2, 3, 400.0))
    n.add_segment(_seg(12, 2, 3, 1200.0))
    n.add_segment(_seg(13, 2, 4, 300.0, one_way=True))
    n.add_segment(_seg(14, 1, 3, 600.0, width=1))
    return n.freeze()


def test_wide_truck_takes_short_path_and_cannot_use_narrow_shortcut(net):
    r = Router(net).route(1, 3, TRUCKS["CAT_793F"], loaded=True)
    assert r is not None
    assert [u.segment_id for u in r.uses] == [10, 11]
    assert r.length_m == 900.0


def test_narrow_ug_truck_takes_the_shortcut(net):
    r = Router(net).route(1, 3, TRUCKS["UG_TRUCK_50"], loaded=True)
    assert [u.segment_id for u in r.uses] == [14]
    assert r.length_m == 600.0


def test_closure_forces_detour_and_cache_keys_differ(net):
    router = Router(net)
    base = router.route(1, 3, TRUCKS["CAT_793F"], loaded=True)
    detour = router.route(1, 3, TRUCKS["CAT_793F"], loaded=True, closed=frozenset({11}))
    assert [u.segment_id for u in detour.uses] == [10, 12]
    assert detour.time_s > base.time_s
    # cache: same query returns the identical object
    assert router.route(1, 3, TRUCKS["CAT_793F"], loaded=True) is base


def test_one_way_blocks_reverse(net):
    assert Router(net).route(4, 2, TRUCKS["CAT_793F"], loaded=False) is None


def test_junction_cross_time_added(net):
    plain = Router(net).route(1, 3, TRUCKS["CAT_793F"], loaded=True)
    with_jx = Router(net, junctions={2: Junction(id=2, cross_s=12.0)}).route(
        1, 3, TRUCKS["CAT_793F"], loaded=True)
    assert with_jx.time_s == pytest.approx(plain.time_s + 12.0, abs=1e-9)


def test_speed_caps_reroute_around_slow_zone_or_bind_when_unavoidable(net):
    router = Router(net)
    base = router.route(1, 3, TRUCKS["CAT_793F"], loaded=True)
    # capping 11 makes the wide 40 km/h detour (12) FASTER than the capped short leg -> reroute
    rerouted = router.route(1, 3, TRUCKS["CAT_793F"], loaded=True, speed_caps={10: 10.0, 11: 10.0})
    assert rerouted.time_s > base.time_s
    assert [u.segment_id for u in rerouted.uses] == [10, 12]
    # capping the detour too leaves no fast alternative: back to [10, 11], fully cap-bound
    allcapped = router.route(1, 3, TRUCKS["CAT_793F"], loaded=True,
                             speed_caps={10: 10.0, 11: 10.0, 12: 10.0})
    assert [u.segment_id for u in allcapped.uses] == [10, 11]
    # hand check: 900 m at 10 km/h (2.78 m/s) free-flow = 324 s
    assert allcapped.time_s == pytest.approx(900.0 / (10.0 / 3.6), abs=1.0)


def test_unknown_node_raises(net):
    with pytest.raises(ValueError):
        Router(net).route(1, 99, TRUCKS["CAT_793F"], loaded=True)


def test_deterministic_tie_break(net):
    # two equal-cost parallel edges: add them fresh with equal lengths, expect lowest segment id
    n = RoadNetwork()
    n.add_node(NodeSite(1, "face", (0, 0, 0)))
    n.add_node(NodeSite(2, "crusher", (0, 0, 0)))
    n.add_segment(_seg(20, 1, 2, 500.0))
    n.add_segment(_seg(21, 1, 2, 500.0))
    r = Router(n.freeze()).route(1, 2, TRUCKS["CAT_777G"], loaded=False)
    assert [u.segment_id for u in r.uses] == [20]
