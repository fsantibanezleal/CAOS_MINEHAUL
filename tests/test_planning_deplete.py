"""U-P1 acceptance (design tests 5-10): conservation to 1e-9 after EVERY transition, monotone
depletion, over-ask clamping, the full illegal-order matrix (each error naming its rule), period
accounting, the hand face-advance anchor, and snapshot round-trip."""
import pytest

from minehaulsim.planning.phase import MinePlan, Period
from minehaulsim.planning.state import BlockCompletedError, PitState, PlanOrderError
from tests.test_planning_model import hand_model

PLAN = MinePlan("pl", (Period(0, 3600.0, (1,)), Period(1, 3600.0, (1, 2))))


def _state() -> PitState:
    return PitState(hand_model(), PLAN)


def test_conservation_after_every_call_of_a_scripted_sequence():
    st = _state()
    total = st.model.total_tonnes
    script = [(10, 30.0)] * 3 + [(10, 10.0)] + [(11, 90.0), (11, 200.0)] + [(12, 150.0), (12, 150.0)]
    for blk, take in script:
        st.deplete(blk, take)
        assert st.mined_t() + sum(st.remaining_t(b.id) for b in st.model.blocks) == pytest.approx(total, abs=1e-9)


def test_monotone_depletion_only_target_changes():
    st = _state()
    before = {b.id: st.remaining_t(b.id) for b in st.model.blocks}
    st.deplete(10, 40.0)
    for bid, rem in before.items():
        expect = rem - 40.0 if bid == 10 else rem
        assert st.remaining_t(bid) == pytest.approx(expect, abs=1e-12)


def test_over_ask_clamps_exactly_and_completes():
    st = _state()
    st.deplete(10, 60.0)
    res = st.deplete(10, 40.0 + 500.0)          # over-ask by 500
    assert res.taken_t == pytest.approx(40.0)
    assert st.remaining_t(10) == 0.0
    assert res.block_completed and not res.bench_completed
    with pytest.raises(BlockCompletedError):
        st.deplete(10, 1.0)


def test_illegal_order_matrix_names_each_rule():
    st = _state()
    # block-order: block 11 before block 10 on bench 1
    with pytest.raises(PlanOrderError, match="block-order"):
        st.deplete(11, 10.0)
    # bench-order: bench 2's block before bench 1 complete
    with pytest.raises(PlanOrderError, match="bench-order"):
        st.deplete(12, 10.0)
    # phase-not-active: phase 2 not active in period 0 (and phase 1 incomplete -> requires fires first)
    with pytest.raises(PlanOrderError, match="phase-requires"):
        st.deplete(14, 10.0)
    # complete phase 1 fully, still period 0 -> phase 2 now blocked by phase-not-active
    for blk, t in [(10, 100.0), (11, 200.0), (12, 300.0), (13, 400.0)]:
        st.deplete(blk, t)
    with pytest.raises(PlanOrderError, match="phase-not-active"):
        st.deplete(14, 10.0)
    with pytest.raises(ValueError):
        st.deplete(14, -5.0)
    with pytest.raises(KeyError):
        st.deplete(999, 5.0)


def test_period_accounting_exact_and_advance_guard():
    st = _state()
    st.deplete(10, 100.0)              # ore, period 0
    st.deplete(11, 50.0)               # waste, period 0
    st.advance_period()
    st.deplete(11, 150.0)              # waste, period 1
    mbp = st.mined_by_period()
    assert mbp[0] == {"ore": 100.0, "waste": 50.0}
    assert mbp[1] == {"ore": 0.0, "waste": 150.0}
    with pytest.raises(IndexError):
        st.advance_period()            # only 2 periods exist


def test_face_advance_hand_anchor():
    # bench 1 polyline (0,0,-15)->(100,0,-15); block 10 tiles [0,50] with 100 t.
    st = _state()
    st.deplete(10, 50.0)               # half the block -> frontier at arc 25.0
    p = st.face_pos(1)
    assert (p.x, p.y, p.z) == (pytest.approx(25.0), pytest.approx(0.0), pytest.approx(-15.0))
    st.deplete(10, 50.0)               # block complete -> frontier at block 11's start fraction 0 = arc 50
    p2 = st.face_pos(1)
    assert p2.x == pytest.approx(50.0)
    # diggable set follows the frontier
    assert st.diggable_blocks() == (11,)


def test_active_faces_and_diggable_respect_plan():
    st = _state()
    assert st.diggable_blocks() == (10,)          # only phase 1's face in period 0
    faces = st.active_faces()
    assert len(faces) == 1 and faces[0].phase_id == 1 and faces[0].block_id == 10
    # complete phase 1, advance to period 1 -> phase 2's face becomes diggable
    for blk, t in [(10, 100.0), (11, 200.0), (12, 300.0), (13, 400.0)]:
        st.deplete(blk, t)
    st.advance_period()
    assert st.diggable_blocks() == (14,)


def test_snapshot_round_trip_resumes_exactly():
    st = _state()
    st.deplete(10, 70.0)
    st.deplete(10, 30.0)
    st.deplete(11, 60.0)
    snap = st.to_dict()
    st2 = PitState.from_dict(hand_model(), PLAN, snap)
    assert st2.mined_t() == pytest.approx(st.mined_t(), abs=1e-12)
    assert st2.remaining_t(11) == pytest.approx(st.remaining_t(11))
    assert st2.diggable_blocks() == st.diggable_blocks()
    assert st2.journal == st.journal
    # and it continues legally from where it left off
    st2.deplete(11, 140.0)
    assert st2.is_complete(11, "block") and st2.is_complete(1, "bench")
