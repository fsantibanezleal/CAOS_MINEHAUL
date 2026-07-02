"""Structural tests for the constrained network graph (U3 acceptance)."""
import numpy as np
import pytest

from minehaulsim.network.constraints import DirectionZone, Junction, ZonePolicy, segment_capacity
from minehaulsim.network.graph import NodeSite, RoadNetwork, Segment


def _seg(sid, a, b, length=500.0, grade=8.0, width=2, one_way=False, zone=None):
    poly = np.array([[0.0, 0.0, 0.0], [length, 0.0, length * grade / 100.0]])
    return Segment(id=sid, a=a, b=b, polyline=poly, length_m=length, grade_pct=grade,
                   width_class=width, one_way=one_way, speed_limit_kmh=50.0, zone_id=zone)


def _net():
    net = RoadNetwork()
    for nid, kind in [(1, "face"), (2, "junction"), (3, "crusher"), (4, "dump")]:
        net.add_node(NodeSite(nid, kind, (float(nid) * 100, 0.0, 0.0)))
    net.add_segment(_seg(10, 1, 2))                     # bidirectional
    net.add_segment(_seg(11, 2, 3, one_way=True))       # one-way ramp
    net.add_segment(_seg(12, 2, 4, width=1, zone=7))    # single-lane, in a DirectionZone
    return net.freeze()


def test_adjacency_honors_one_way():
    net = _net()
    # leaving node 3: segment 11 is one-way 2->3, so NOTHING leaves 3 via it
    assert list(net.leaving(3)) == []
    # leaving node 2: both directions of 10? no — 10 is a->b 1->2, so from 2 it's direction -1
    leaving2 = {(s.id, d) for s, d in net.leaving(2)}
    assert (10, -1) in leaving2 and (11, +1) in leaving2 and (12, +1) in leaving2


def test_grade_is_signed_by_direction():
    net = _net()
    s = net.segments[10]
    assert s.grade_for(+1) == 8.0 and s.grade_for(-1) == -8.0


def test_frozen_network_rejects_mutation_and_duplicates_rejected():
    net = _net()
    with pytest.raises(RuntimeError):
        net.add_node(NodeSite(99, "junction", (0, 0, 0)))
    fresh = RoadNetwork()
    fresh.add_node(NodeSite(1, "face", (0, 0, 0)))
    with pytest.raises(ValueError):
        fresh.add_node(NodeSite(1, "face", (0, 0, 0)))
    with pytest.raises(ValueError):
        fresh.add_segment(_seg(1, 1, 2))   # endpoint 2 missing


def test_validate_flags_zone_width_violation_and_isolated_site():
    net = RoadNetwork()
    net.add_node(NodeSite(1, "face", (0, 0, 0)))
    net.add_node(NodeSite(2, "crusher", (100, 0, 0)))
    net.add_node(NodeSite(3, "dump", (200, 0, 0)))      # isolated
    bad = _seg(10, 1, 2, width=2, zone=5)               # width-2 inside a zone = violation
    net.add_segment(bad)
    issues = net.freeze().validate()
    assert any("width_class=2" in i for i in issues)
    assert any("dump node 3" in i for i in issues)


def test_json_round_trip_preserves_structure():
    net = _net()
    clone = RoadNetwork.from_dict(net.to_dict())
    assert set(clone.nodes) == set(net.nodes)
    assert set(clone.segments) == set(net.segments)
    assert clone.segments[12].zone_id == 7
    assert clone.out_adj == net.out_adj


def test_zone_and_junction_specs():
    z = DirectionZone(id=7, segment_ids=(12,), policy=ZonePolicy.LOADED_PRIORITY, max_in_zone=2)
    z2 = DirectionZone.from_dict(z.to_dict())
    assert z2 == z
    j = Junction.from_dict(Junction(id=2, capacity=1, cross_s=12.0).to_dict())
    assert j.id == 2 and j.capacity == 1
    with pytest.raises(ValueError):
        DirectionZone(id=1, segment_ids=())
    assert segment_capacity(500.0) == 6      # floor(500/80)
    assert segment_capacity(40.0) == 1       # never below 1
