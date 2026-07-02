"""Kinematics verified against HAND-COMPUTED values (the U2 acceptance gate).

Hand computation for the anchor case (CAT_793F loaded on a 10% ramp, 2% rolling):
    GVW = 165 + 227 = 392 t;  F_req = 392 * 9.80665 * 12/100 = 461.3 kN.
    793F rimpull envelope: F(v) = min(traction_cap, 0.85 * 1976 kW / v).
    Power-limited speed: v = 0.85*1976/461.3 = 3.64 m/s = 13.1 km/h  (traction cap ~1268 kN, inactive).
    So the solver must land near 13.1 km/h (within the sampled-envelope interpolation tolerance).
"""
import pytest

from minehaulsim.equipment import LHDS, LOADERS, TRUCKS
from minehaulsim.network import SpeedSolver, attainable_speed_kmh, traverse_time_s


def test_catalog_curves_are_monotone_decreasing():
    for t in list(TRUCKS.values()) + list(LHDS.values()):
        for curve in (t.rimpull_kn, t.retarder_kn):
            speeds = [v for v, _ in curve]
            forces = [f for _, f in curve]
            assert speeds == sorted(speeds)
            assert forces == sorted(forces, reverse=True), t.name


def test_793f_loaded_10pct_ramp_matches_hand_calc():
    t = TRUCKS["CAT_793F"]
    v = attainable_speed_kmh(t, gvw_t=392.0, grade_pct=10.0, rolling_pct=2.0)
    assert v == pytest.approx(13.1, abs=0.8)   # power-limited: 0.85*1976/461.3 kN -> 13.1 km/h


def test_steeper_grade_is_slower_and_empty_is_faster():
    t = TRUCKS["CAT_785D"]
    v8 = attainable_speed_kmh(t, 242.0, 8.0, 2.0)
    v12 = attainable_speed_kmh(t, 242.0, 12.0, 2.0)
    assert v12 < v8
    v_empty = attainable_speed_kmh(t, t.empty_t, 8.0, 2.0)
    assert v_empty > v8


def test_downhill_is_retarder_limited_not_free():
    t = TRUCKS["CAT_793F"]
    v_down = attainable_speed_kmh(t, 392.0, -10.0, 2.0)   # descending loaded
    assert 0 < v_down < t.max_speed_kmh                    # held by the retarder, not unlimited


def test_speed_solver_applies_limits_and_caches():
    s = SpeedSolver()
    t = TRUCKS["CAT_777G"]
    v = s.speed_ms(t, 172.0, 0.0, 2.0, limit_kmh=30.0)
    assert v <= 30.0 / 3.6 + 1e-9                          # segment limit binds on the flat
    assert s.speed_ms(t, 172.0, 0.0, 2.0, 30.0) == v       # cached, identical


def test_traverse_time_includes_acceleration_penalty_and_rejects_stall():
    # 1000 m at 10 m/s from standstill, loaded: 100 s cruise + 10/0.35 = 28.57 s accel
    assert traverse_time_s(1000.0, 10.0, 0.0, loaded=True) == pytest.approx(128.57, abs=0.01)
    # entering at speed: no penalty
    assert traverse_time_s(1000.0, 10.0, 10.0, loaded=True) == pytest.approx(100.0, abs=1e-9)
    with pytest.raises(ValueError):
        traverse_time_s(100.0, 0.0, 0.0, loaded=True)


def test_loader_catalog_sane():
    sh = LOADERS["SHOVEL_45"]
    assert sh.pass_t > 0 and sh.pass_time_s > 0
    # a 793F (227 t) needs ceil(227/45)=6 passes
    import math
    assert math.ceil(TRUCKS["CAT_793F"].payload_mean_t / sh.pass_t) == 6
