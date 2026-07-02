"""U-P4 acceptance: pit_summary anchors, reachability under overlay+damage, plan_feasibility
(availability prefix, shortfalls, capacity lower-bound honesty), and the PlanContext coupling."""
import pytest

from minehaulsim.network.routing import Router
from minehaulsim.planning.damage import DamageSeverity
from minehaulsim.planning.evaluate import pit_summary, plan_feasibility, reachability
from minehaulsim.planning.phase import MinePlan, Period
from minehaulsim.planning.state import PitState
from tests.test_planning_damage_zones import _event, _wall_net
from tests.test_planning_model import hand_model

PLAN = MinePlan("pl", (Period(0, 3600.0, (1,), target_ore_t=400.0, target_waste_t=600.0),
                       Period(1, 3600.0, (1, 2), target_ore_t=1100.0)))
FLEET = {"CAT_793F": 3}
DUMPS = (200,)


def test_pit_summary_matches_hand_model_and_state():
    m = hand_model()
    s0 = pit_summary(m)
    assert s0.total_t == 2100.0 and s0.ore_t == 1500.0 and s0.waste_t == 600.0
    assert s0.n_benches == 3 and s0.n_blocks == 6 and s0.n_phases == 2
    assert s0.remaining_t is None
    st = PitState(m, PLAN)
    st.deplete(10, 100.0)
    s1 = pit_summary(m, st)
    assert s1.remaining_t == pytest.approx(2000.0)
    assert s1.active_faces and all(f >= 100_000 for f in s1.active_faces)


def test_reachability_reports_per_class_and_respects_closures():
    m, net = hand_model(), _wall_net()
    st = PitState(m, PLAN)
    eff = st.overlay().effective_network(net)
    router = Router(eff)
    closed, caps = st.routing_inputs()
    faces = tuple(f.face_node for f in st.active_faces())
    rep = reachability(eff, router, faces, DUMPS, FLEET, closed=closed, speed_caps=caps)
    assert rep.all_reachable                       # bench-1 face -> anchor 101 -> ... -> crusher
    st.apply_damage(_event(DamageSeverity.WALL_COLLAPSE), net)
    closed2, caps2 = st.routing_inputs()
    rep2 = reachability(eff, router, faces, DUMPS, FLEET, closed=closed2, speed_caps=caps2)
    assert not rep2.all_reachable                  # the wall collapse severs the bench access
    assert rep2.to_dict()["all_reachable"] is False


def test_plan_feasibility_availability_shortfall_and_consumption_across_periods():
    m, net = hand_model(), _wall_net()
    router = Router(net)
    rep = plan_feasibility(m, PLAN, net, router, FLEET, DUMPS)
    c0 = rep.checks[0]
    # period 0: only phase 1 active -> its full 1000 t (300 ore / 700? no: ore=100+300=400, waste=200+400=600)
    assert c0.available_ore_t == pytest.approx(400.0)
    assert c0.available_waste_t == pytest.approx(600.0)
    assert c0.shortfall_ore_t == 0.0 and c0.shortfall_waste_t == 0.0
    # period 1: phase 2 becomes available AFTER phase 1 consumed -> 1100 t ore
    c1 = rep.checks[1]
    assert c1.available_ore_t == pytest.approx(1100.0)
    assert c1.shortfall_ore_t == 0.0
    # cycles + capacity present and positive for reachable faces
    assert c0.est_cycle_s and c0.fleet_capacity_t > 0


def test_plan_feasibility_flags_shortfall_when_targets_exceed_reserves():
    m, net = hand_model(), _wall_net()
    greedy = MinePlan("greedy", (Period(0, 3600.0, (1,), target_ore_t=2000.0),))
    rep = plan_feasibility(m, greedy, net, Router(net), FLEET, DUMPS)
    assert not rep.feasible
    assert rep.checks[0].shortfall_ore_t == pytest.approx(2000.0 - 400.0)


def test_plancontext_couples_sim_loads_to_depletion():
    m = hand_model()
    st = PitState(m, PLAN)
    face = st.active_faces()[0].face_node
    assert st.is_diggable(face)
    blk = st.block_at_face(face)
    assert blk == 10
    res = st.on_load(face, 100.0)                   # a truckload depletes the SAME tonnes
    assert res.taken_t == pytest.approx(100.0)
    assert res.block_completed                      # block 10 was exactly 100 t
    assert st.mined_t() == pytest.approx(100.0)
    with pytest.raises(KeyError):
        st.block_at_face(999)
    assert not st.is_diggable(999)
