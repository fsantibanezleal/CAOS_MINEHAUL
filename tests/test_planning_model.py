"""U-P1 acceptance (design tests 1-4): construction rules, exact reserve partition, JSON identity,
plan validation incl. precedence impossibility. Hand model: 3 benches / 6 blocks / 100..600 t = 2100 t."""
import numpy as np
import pytest

from minehaulsim.planning.phase import MinePlan, Period, Phase
from minehaulsim.planning.pit_model import Bench, DigBlock, PitModel


def _poly(length=100.0, z=-15.0):
    return np.array([[0.0, 0.0, z], [length, 0.0, z]])


def _blk(bid, bench, seq, tonnes, mat="ore", s0=None, s1=None):
    s0 = 50.0 * seq if s0 is None else s0
    s1 = 50.0 * (seq + 1) if s1 is None else s1
    return DigBlock(id=bid, bench_id=bench, seq=seq, tonnes=tonnes, material=mat,
                    ore_grade=0.8 if mat == "ore" else 0.0, arc_s0=s0, arc_s1=s1)


def hand_model() -> PitModel:
    benches = (
        Bench(1, -15.0, 15.0, _poly(), anchor_node=101, block_ids=(10, 11)),
        Bench(2, -30.0, 15.0, _poly(z=-30.0), anchor_node=102, block_ids=(12, 13)),
        Bench(3, -15.0, 15.0, _poly(z=-15.0), anchor_node=103, block_ids=(14, 15)),
    )
    blocks = (
        _blk(10, 1, 0, 100.0), _blk(11, 1, 1, 200.0, "waste"),
        _blk(12, 2, 0, 300.0), _blk(13, 2, 1, 400.0, "waste"),
        _blk(14, 3, 0, 500.0), _blk(15, 3, 1, 600.0),
    )
    phases = (Phase(1, "P1", (1, 2)), Phase(2, "P2", (3,), requires=(1,)))
    return PitModel(id="hand", benches=benches, blocks=blocks, phases=phases)


def test_construction_rules_reject_each_named_violation():
    m = hand_model()
    # dup block id
    with pytest.raises(ValueError, match="unique_ids"):
        PitModel("x", m.benches, m.blocks + (_blk(10, 1, 2, 50.0),), m.phases)
    # unknown bench ref
    with pytest.raises(ValueError, match="block_bench_exists"):
        PitModel("x", m.benches, m.blocks[:-1] + (_blk(99, 77, 1, 50.0),), m.phases)
    # seq gap (0, 2)
    bad = (_blk(10, 1, 0, 100.0), _blk(11, 1, 2, 200.0))
    with pytest.raises(ValueError, match="seq must be contiguous"):
        PitModel("x", m.benches[:1], bad, (Phase(1, "P", (1,)),))
    # overlapping arcs
    bad2 = (_blk(10, 1, 0, 100.0, s0=0.0, s1=60.0), _blk(11, 1, 1, 200.0, s0=50.0, s1=100.0))
    with pytest.raises(ValueError, match="overlap"):
        PitModel("x", m.benches[:1], bad2, (Phase(1, "P", (1,)),))
    # non-positive tonnes / bad material
    with pytest.raises(ValueError, match="tonnes_positive"):
        PitModel("x", m.benches[:1], (_blk(10, 1, 0, 0.0), _blk(11, 1, 1, 1.0)), (Phase(1, "P", (1,)),))
    with pytest.raises(ValueError, match="material_enum"):
        PitModel("x", m.benches[:1], (_blk(10, 1, 0, 1.0, mat="air"), _blk(11, 1, 1, 1.0)), (Phase(1, "P", (1,)),))
    # bench in two phases / orphan bench
    with pytest.raises(ValueError, match="bench_in_exactly_one_phase"):
        PitModel("x", m.benches, m.blocks, (Phase(1, "A", (1, 2)), Phase(2, "B", (2, 3))))
    with pytest.raises(ValueError, match="not owned by any phase"):
        PitModel("x", m.benches, m.blocks, (Phase(1, "A", (1, 2)),))


def test_reserve_partition_exact_on_hand_model():
    m = hand_model()
    assert m.total_tonnes == 2100.0
    assert sum(m.tonnes_by_bench.values()) == m.total_tonnes
    assert sum(m.tonnes_by_phase.values()) == m.total_tonnes
    assert m.tonnes_by_phase == {1: 1000.0, 2: 1100.0}
    assert m.ore_tonnes == 1500.0 and m.waste_tonnes == 600.0
    assert m.strip_ratio == pytest.approx(600.0 / 1500.0)


def test_json_round_trip_identity():
    m = hand_model()
    m2 = PitModel.from_dict(m.to_dict())
    assert m2.total_tonnes == m.total_tonnes
    assert [b.id for b in m2.blocks] == [b.id for b in m.blocks]
    assert np.array_equal(m2.bench(1).polyline, m.bench(1).polyline)
    plan = MinePlan("pl", (Period(0, 3600.0, (1,)), Period(1, 3600.0, (1, 2))))
    assert MinePlan.from_dict(plan.to_dict()) == plan


def test_plan_validation_and_precedence_impossibility():
    m = hand_model()
    assert MinePlan("ok", (Period(0, 10.0, (1,)), Period(1, 10.0, (1, 2)))).validate(m) == []
    # unknown phase
    assert any("unknown phase" in i for i in MinePlan("u", (Period(0, 10.0, (9,)),)).validate(m))
    # phase 2 (requires 1) active in period 0 while phase 1 first active LATER -> impossible
    bad = MinePlan("bad", (Period(0, 10.0, (2,)), Period(1, 10.0, (1,))))
    assert any("precedence-impossible" in i for i in bad.validate(m))
    with pytest.raises(ValueError, match="contiguous"):
        MinePlan("nc", (Period(0, 10.0, (1,)), Period(2, 10.0, (1,))))
    with pytest.raises(ValueError, match="duration"):
        Period(0, 0.0, (1,))
