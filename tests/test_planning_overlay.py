"""U-P2 acceptance (design tests 10-14): reposition quantization, spur re-anchoring with exact arc
lengths, bench-completion cascade + topo delta, monotone revision, effective_network determinism,
planning id-base reservation, and the extended snapshot round-trip."""
import numpy as np
import pytest

from minehaulsim.network.graph import NodeSite, RoadNetwork, Segment
from minehaulsim.planning.overlay import PLANNING_NODE_ID_BASE, PLANNING_SEG_ID_BASE
from minehaulsim.planning.phase import MinePlan, Period
from minehaulsim.planning.state import PitState
from tests.test_planning_model import hand_model

PLAN = MinePlan("pl", (Period(0, 3600.0, (1,)), Period(1, 3600.0, (1, 2))))


def _state(step=25.0) -> PitState:
    return PitState(hand_model(), PLAN, reposition_step_m=step)


def _base_net() -> RoadNetwork:
    net = RoadNetwork()
    for nid in (101, 102, 103):
        net.add_node(NodeSite(nid, "junction", (0.0, 0.0, 0.0)))
    net.add_node(NodeSite(200, "crusher", (500.0, 0.0, 0.0)))
    poly = np.array([[0.0, 0.0, 0.0], [500.0, 0.0, 0.0]])
    net.add_segment(Segment(id=1, a=101, b=200, polyline=poly, length_m=500.0, grade_pct=0.0,
                            width_class=2, one_way=False, speed_limit_kmh=50.0))
    net.add_segment(Segment(id=2, a=102, b=200, polyline=poly, length_m=600.0, grade_pct=0.0,
                            width_class=2, one_way=False, speed_limit_kmh=50.0))
    net.add_segment(Segment(id=3, a=103, b=200, polyline=poly, length_m=700.0, grade_pct=0.0,
                            width_class=2, one_way=False, speed_limit_kmh=50.0))
    return net.freeze()


def test_initial_materialization_creates_face_and_spur_with_reserved_ids():
    st = _state()
    ov = st.overlay()
    assert ov.revision == 1                                   # one materialization (phase 1 bench 1)
    assert len(ov.added_segments) == 1
    spur = ov.added_segments[0]
    assert spur.id >= PLANNING_SEG_ID_BASE
    assert spur.b == 101                                      # bench 1 anchor
    assert ov.moved_nodes[0][0] >= PLANNING_NODE_ID_BASE
    # face at arc 0 -> spur length clamps to the 1 m minimum
    assert spur.length_m == pytest.approx(1.0)


def test_sub_step_drift_does_not_bump_revision():
    st = _state(step=25.0)
    rev0 = st.overlay().revision
    res = st.deplete(10, 20.0)                                # block 10: 100 t over arc [0,50] -> 10 m drift
    assert not res.overlay_changed
    assert st.overlay().revision == rev0


def test_crossing_the_step_re_anchors_the_spur_with_exact_arc_length():
    st = _state(step=25.0)
    res = st.deplete(10, 60.0)                                # 60% of [0,50] = arc 30 -> crosses 25 m
    assert res.overlay_changed
    ov = st.overlay()
    live = [s for s in ov.added_segments if s.id not in ov.retired_segments]
    assert len(live) == 1
    assert live[0].length_m == pytest.approx(30.0, abs=1e-9)  # arc distance face->anchor, exact
    assert len(ov.retired_segments) == 1                      # the old spur retired


def test_bench_completion_retires_spur_records_topo_delta_and_activates_next():
    st = _state()
    st.deplete(10, 100.0)
    res = st.deplete(11, 200.0)                               # completes bench 1
    assert res.bench_completed and res.overlay_changed
    delta = st.topo_delta()
    assert delta and delta[0]["bench_id"] == 1 and delta[0]["event"] == "bench_mined_out"
    # bench 2's face materialized: a live spur anchored at node 102
    ov = st.overlay()
    live = [s for s in ov.added_segments if s.id not in ov.retired_segments]
    assert len(live) == 1 and live[0].b == 102


def test_revision_is_monotone_across_a_full_phase():
    st = _state()
    revs = [st.overlay().revision]
    for blk, t in [(10, 100.0), (11, 200.0), (12, 300.0), (13, 400.0)]:
        st.deplete(blk, t)
        revs.append(st.overlay().revision)
    assert revs == sorted(revs)
    assert revs[-1] > revs[0]


def test_effective_network_routes_to_the_moving_face():
    st = _state()
    base = _base_net()
    assert st.model.bind_check(base) == []
    ov = st.overlay()
    eff = ov.effective_network(base)
    face_node = ov.moved_nodes[0][0]
    assert face_node in eff.nodes
    # the spur connects the face to anchor 101, which connects to the crusher
    from minehaulsim.equipment import TRUCKS
    from minehaulsim.network.routing import Router
    closed, caps = ov.routing_inputs()
    r = Router(eff).route(face_node, 200, TRUCKS["CAT_777G"], loaded=True,
                          closed=closed, speed_caps=caps)
    assert r is not None
    assert [u.segment_id for u in r.uses][-1] == 1            # exits via anchor 101's segment


def test_advance_period_materializes_new_phase_faces():
    st = _state()
    for blk, t in [(10, 100.0), (11, 200.0), (12, 300.0), (13, 400.0)]:
        st.deplete(blk, t)                                    # phase 1 complete
    st.advance_period()                                       # phase 2 becomes active
    ov = st.overlay()
    live = [s for s in ov.added_segments if s.id not in ov.retired_segments]
    assert len(live) == 1 and live[0].b == 103                # bench 3's anchor


def test_extended_snapshot_round_trip_preserves_overlay_and_counters():
    st = _state()
    st.deplete(10, 60.0)                                      # cross the step once
    snap = st.to_dict()
    st2 = PitState.from_dict(hand_model(), PLAN, snap)
    ov1, ov2 = st.overlay(), st2.overlay()
    assert ov2.revision == ov1.revision
    assert ov2.moved_nodes == ov1.moved_nodes
    assert [s.id for s in ov2.added_segments] == [s.id for s in ov1.added_segments]
    assert ov2.retired_segments == ov1.retired_segments
    # counters continue without collision after resume
    st2.deplete(10, 40.0)
    st2.deplete(11, 200.0)                                    # completes bench 1 -> new ids allocated
    new_ids = [s.id for s in st2.overlay().added_segments]
    assert len(new_ids) == len(set(new_ids))
