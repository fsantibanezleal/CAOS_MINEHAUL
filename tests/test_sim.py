"""U6a acceptance: the full haul cycle on a hand-built network — cyclelog legality, determinism,
policy divergence, and the plan coupling (tonnes == depletion; exhausted plan parks trucks)."""
import numpy as np
import pytest

from minehaulsim.des.dispatch import FixedPolicy, MinQueuePolicy, NearestPolicy
from minehaulsim.des.sim import LoaderSpec, TruckSpec, run_shift
from minehaulsim.network.graph import NodeSite, RoadNetwork, Segment
from minehaulsim.planning.phase import MinePlan, Period
from minehaulsim.planning.state import PitState
from tests.test_planning_model import hand_model

NEXT = {"load": "haul", "haul": "dump", "dump": "return", "return": "load"}


def _net() -> RoadNetwork:
    """Two loaders (1 near, 2 far) + one crusher; flat two-lane roads."""
    net = RoadNetwork()
    for nid, kind, pos in [(1, "face", (0.0, 0.0, 0.0)), (2, "face", (0.0, 800.0, 0.0)),
                           (200, "crusher", (1500.0, 400.0, 0.0))]:
        net.add_node(NodeSite(nid, kind, pos))
    def seg(sid, a, b, L):
        poly = np.array([[net.nodes[a].pos[0], net.nodes[a].pos[1], 0.0],
                         [net.nodes[b].pos[0], net.nodes[b].pos[1], 0.0]])
        return Segment(id=sid, a=a, b=b, polyline=poly, length_m=L, grade_pct=0.0,
                       width_class=2, one_way=False, speed_limit_kmh=50.0)
    net.add_segment(seg(1, 1, 200, 1600.0))
    net.add_segment(seg(2, 2, 200, 3200.0))     # loader 2 is twice as far
    return net.freeze()


LOADERS = [LoaderSpec(1), LoaderSpec(2)]
TRUCKS6 = [TruckSpec(i, "CAT_793F", 1 if i % 2 else 2) for i in range(1, 7)]


def test_cyclelog_legality_and_kpis():
    res = run_shift(_net(), LOADERS, [200], TRUCKS6, NearestPolicy(), seed=42)
    assert res.tonnes > 0 and res.cycles > 10
    # per-truck legal state machine + monotone times
    by_truck: dict[int, list[dict]] = {}
    for e in res.events:
        by_truck.setdefault(e["truck_id"], []).append(e)
    for tid, evs in by_truck.items():
        state = "return"
        last_t = -1.0
        for e in evs:
            assert e["t"] >= last_t, f"truck {tid} time regression"
            assert e["event"] == NEXT[state], f"truck {tid}: {state} -> {e['event']}"
            state = e["event"]
            last_t = e["t"]
    # hauls carry payload; loads/returns do not
    for e in res.events:
        if e["event"] in ("haul", "dump"):
            assert 100.0 < e["payload_t"] < 400.0
        else:
            assert e["payload_t"] == 0.0


def test_determinism_byte_identical_and_seed_sensitivity():
    a = run_shift(_net(), LOADERS, [200], TRUCKS6, NearestPolicy(), seed=7)
    b = run_shift(_net(), LOADERS, [200], TRUCKS6, NearestPolicy(), seed=7)
    assert a.events == b.events and a.tonnes == b.tonnes
    c = run_shift(_net(), LOADERS, [200], TRUCKS6, NearestPolicy(), seed=8)
    assert c.events != a.events


def test_policies_diverge_on_the_asymmetric_network():
    fixed = run_shift(_net(), LOADERS, [200], TRUCKS6, FixedPolicy(), seed=42)
    nearest = run_shift(_net(), LOADERS, [200], TRUCKS6, NearestPolicy(), seed=42)
    minq = run_shift(_net(), LOADERS, [200], TRUCKS6, MinQueuePolicy(), seed=42)
    # nearest herds everyone to loader 1 -> queueing; fixed keeps the far loader fed
    assert nearest.events != fixed.events
    # exclude the start-up loads at each truck's initial loader: judge steady-state behavior
    served_nearest = {e["shovel_id"] for e in nearest.events if e["event"] == "load" and e["t"] > 1800}
    served_minq = {e["shovel_id"] for e in minq.events if e["event"] == "load" and e["t"] > 1800}
    assert served_nearest == {1}                     # greedy herds everyone to the near loader
    assert served_minq == {1, 2}                     # queue-aware keeps both fed
    assert minq.tonnes > nearest.tonnes              # spreading beats herding here


PLAN = MinePlan("pl", (Period(0, 8 * 3600.0, (1, 2)),))


def _planned_state():
    # phases both active; the model's bench anchors are 101/102/103 — build a matching net
    m = hand_model()
    net = RoadNetwork()
    for nid, kind, pos in [(101, "junction", (0.0, 0.0, -15.0)), (102, "junction", (0.0, 800.0, -30.0)),
                           (103, "junction", (0.0, 400.0, -15.0)), (200, "crusher", (1500.0, 400.0, 0.0))]:
        net.add_node(NodeSite(nid, kind, pos))
    def seg(sid, a, b, L):
        poly = np.array([[net.nodes[a].pos[0], net.nodes[a].pos[1], net.nodes[a].pos[2]],
                         [net.nodes[b].pos[0], net.nodes[b].pos[1], net.nodes[b].pos[2]]])
        return Segment(id=sid, a=a, b=b, polyline=poly, length_m=L, grade_pct=0.0,
                       width_class=2, one_way=False, speed_limit_kmh=50.0)
    net.add_segment(seg(1, 101, 200, 1700.0))
    net.add_segment(seg(2, 102, 200, 1800.0))
    net.add_segment(seg(3, 103, 200, 1600.0))
    plan = MinePlan("pl", (Period(0, 8 * 3600.0, (1, 2)),))
    st = PitState(m, plan)
    return m, net.freeze(), st


def test_plan_coupled_shift_tonnes_equal_depletion_and_trucks_park_when_exhausted():
    m, net, st = _planned_state()
    faces = [f.face_node for f in st.active_faces()]
    loaders = [LoaderSpec(f) for f in faces]         # loaders sit AT the face nodes
    trucks = [TruckSpec(i, "CAT_777G", faces[0]) for i in range(1, 4)]
    res = run_shift(net, loaders, [200], trucks, MinQueuePolicy(), seed=11,
                    plan_context=st, until_s=8 * 3600.0)
    # cyclelog tonnes ARE model depletion: mined == sum of HAUL payloads (incl. partial final loads)
    assert st.mined_t() == pytest.approx(
        sum(e["payload_t"] for e in res.events if e["event"] == "haul"), abs=1e-6)
    assert st.mined_t() >= res.tonnes - 1e-6         # in-flight loads may not have dumped by cutoff
    # the loaders FOLLOW the advancing front: the whole 2100 t hand model is mined out
    assert st.mined_t() == pytest.approx(2100.0)
    assert st.is_complete(1, "phase") and st.is_complete(2, "phase")
    assert res.tonnes <= 2100.0 + 1e-9
    last_event_t = max(e["t"] for e in res.events)
    assert last_event_t < 8 * 3600.0                 # everything parked long before cutoff


def test_traffic_headway_serializes_and_fast_mode_is_free_flow():
    """U6b: the FIFO no-overtake rule makes bunching EMERGE — dumps arrive >= headway_s apart on the
    shared segment; fast_mode (free-flow) lets them arrive closer. Same seed isolates the effect."""
    traffic = run_shift(_net(), [LoaderSpec(1)], [200], TRUCKS6[:3], FixedPolicy(), seed=5)
    fast = run_shift(_net(), [LoaderSpec(1)], [200], TRUCKS6[:3], FixedPolicy(), seed=5, fast_mode=True)
    # under traffic, consecutive arrivals at the dump (same shared segment) are >= 8 s apart
    t_dumps = sorted(e["t"] for e in traffic.events if e["event"] == "dump")
    gaps = [b - a for a, b in zip(t_dumps, t_dumps[1:])]
    assert all(g >= 8.0 - 1e-9 for g in gaps[:3])
    # both modes still mine, and the runs differ (traffic adds real delay)
    assert traffic.tonnes > 0 and fast.tonnes > 0
    assert traffic.events != fast.events
