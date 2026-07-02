"""U8 geometry acceptance: rim math against hand values, ring nesting, ramp grades exactly the
design grade, one-way circulation on dual_spiral, and structural validity of built networks."""
import math

import numpy as np
import pytest

from minehaulsim.geometry.openpit import (FLOOR_R_MIN_M, OpenPitDesign, PitGeometryError,
                                          RimShape, build_open_pit)
from minehaulsim.geometry.paths import polyline_length, signed_grade_pct
from minehaulsim.network.routing import Router
from minehaulsim.equipment.catalog import TRUCKS


def _circle(r: float) -> RimShape:
    return RimShape(rx=r, ry=r, n_exp=2.0)


def _design(**kw) -> OpenPitDesign:
    base = dict(shape=_circle(420.0), n_benches=8, bench_height_m=12.0, berm_width_m=10.0,
                face_angle_deg=65.0, ramp_style="spiral", ramp_grade_pct=9.0, ramp_lanes=2,
                faces=((4, 1.2), (7, 3.5)),
                destinations=(("crusher", 0.4, 900.0), ("dump", 1.1, 700.0)),
                n_surface_junctions=2)
    base.update(kw)
    return OpenPitDesign(**base)


# ---- rim math ----

def test_rim_circle_and_superellipse_hand_values():
    # n=2, rx=ry=R is a circle at every azimuth
    R = 300.0
    sh = _circle(R)
    for th in (0.0, 0.7, math.pi / 2, 2.5, math.pi):
        assert sh.radius(th) == pytest.approx(R, rel=1e-12)
    # n=2 ellipse: at the semi-axes the radius IS the semi-axis
    e = RimShape(rx=400.0, ry=250.0, n_exp=2.0)
    assert e.radius(0.0) == pytest.approx(400.0)
    assert e.radius(math.pi / 2) == pytest.approx(250.0)
    # harmonic perturbation: r(0) scales by (1 + a) for a k=2 harmonic with phase 0
    p = RimShape(rx=300.0, ry=300.0, n_exp=2.0, harmonics=((2, 0.1, 0.0),))
    assert p.radius(0.0) == pytest.approx(300.0 * 1.1)
    assert p.radius(math.pi / 2) == pytest.approx(300.0 * 0.9)   # cos(2*pi/2) = -1


def test_rim_perturbation_bound_rejected():
    with pytest.raises(PitGeometryError):
        RimShape(rx=300.0, ry=300.0, n_exp=2.0,
                 harmonics=((2, 0.5, 0.0), (3, 0.5, 0.0)))       # sum 1.0 >= 0.9


def test_sector_boost_expands_only_its_sector():
    sh = RimShape(rx=300.0, ry=300.0, n_exp=2.0, sector_boosts=((1.0, 1.0, 0.2),))
    assert sh.radius(1.0) == pytest.approx(300.0 * 1.2)          # window peak at the center
    assert sh.radius(1.0 + math.pi) == pytest.approx(300.0)      # outside the sector: untouched


# ---- rings + solids ----

def test_rings_nest_by_exactly_step_in_and_floor_gate():
    d = _design()
    geo = build_open_pit(d)
    assert len(geo.rings) == d.n_benches + 1
    th_idx = 17                                                  # arbitrary sampled azimuth
    for i in range(1, len(geo.rings)):
        r_prev = np.linalg.norm(geo.rings[i - 1][th_idx, :2])
        r_here = np.linalg.norm(geo.rings[i][th_idx, :2])
        assert r_prev - r_here == pytest.approx(d.step_in_m, rel=1e-9)
        assert geo.rings[i][th_idx, 2] == pytest.approx(-i * d.bench_height_m)
    assert geo.floor_r_min_m >= FLOOR_R_MIN_M
    # a pit too deep for its rim is rejected by name
    with pytest.raises(PitGeometryError, match="floor radius"):
        build_open_pit(_design(shape=_circle(180.0), n_benches=10))


# ---- ramps ----

@pytest.mark.parametrize("style", ["spiral", "switchback"])
def test_ramp_segments_carry_exactly_the_design_grade(style):
    d = _design(ramp_style=style, ramp_lanes=2)
    geo = build_open_pit(d)
    net = geo.network
    ramp_segs = [s for s in net.segments.values() if abs(s.grade_pct) > 1e-9]
    assert len(ramp_segs) == d.n_benches                         # one per bench span
    for s in ramp_segs:
        assert abs(s.grade_pct) == pytest.approx(d.ramp_grade_pct)
        # polyline end-to-end grade matches the declared grade within sampling tolerance
        assert abs(signed_grade_pct(s.polyline)) == pytest.approx(d.ramp_grade_pct, rel=0.05)
        assert s.length_m == pytest.approx(polyline_length(s.polyline), rel=1e-9)
    # the ramp bottoms out at the floor
    z_min = min(min(s.polyline[:, 2]) for s in ramp_segs)
    assert z_min == pytest.approx(-d.depth_m)


def test_switchback_turns_are_capacity_one_junctions():
    d = _design(ramp_style="switchback")
    geo = build_open_pit(d)
    turn_junctions = [j for j in geo.junctions.values() if j.cross_s == 15.0]
    assert len(turn_junctions) == d.n_benches - 1                # every interior turn
    assert all(j.capacity == 1 for j in turn_junctions)


def test_single_lane_spiral_chains_direction_zones():
    d = _design(ramp_style="spiral", ramp_lanes=1)
    geo = build_open_pit(d)
    ramp_segs = [s for s in geo.network.segments.values() if abs(s.grade_pct) > 1e-9]
    assert all(s.zone_id is not None and s.single_lane_op for s in ramp_segs)
    assert len(geo.zones) == len(ramp_segs)                      # one zone per bench span
    assert geo.network.validate() == []                          # single_lane_op keeps it legal


def test_dual_spiral_single_lane_is_a_one_way_circulation_pair():
    d = _design(ramp_style="dual_spiral", ramp_lanes=1, entry_azimuth2_rad=3.6,
                faces=((8, 2.0),))
    geo = build_open_pit(d)
    net = geo.network
    assert len(geo.portal_ids) == 2
    ramp_segs = [s for s in net.segments.values() if abs(s.grade_pct) > 1e-9]
    assert all(s.one_way for s in ramp_segs)
    down = [s for s in ramp_segs if s.grade_pct < 0]
    up = [s for s in ramp_segs if s.grade_pct > 0]
    assert len(down) == d.n_benches and len(up) == d.n_benches
    # loaded climb and empty descent are BOTH routable and use DISJOINT ramps
    unit = TRUCKS["CAT_793F"]
    router = Router(net)
    face = 1
    crusher = geo.crusher_ids[0]
    r_up = router.route(face, crusher, unit, loaded=True)
    r_down = router.route(crusher, face, unit, loaded=False)
    assert r_up is not None and r_down is not None
    up_ids = {s.id for s in up}
    down_ids = {s.id for s in down}
    used_up = {u.segment_id for u in r_up.uses} & (up_ids | down_ids)
    used_down = {u.segment_id for u in r_down.uses} & (up_ids | down_ids)
    assert used_up <= up_ids and used_up
    assert used_down <= down_ids and used_down


def test_faces_connect_to_every_ramp_and_network_is_structurally_valid():
    d = _design(ramp_style="dual_spiral", ramp_lanes=2, entry_azimuth2_rad=4.0)
    geo = build_open_pit(d)
    net = geo.network
    assert net.validate() == []
    for fid in geo.face_nodes:
        arcs = [s for s in net.segments.values() if fid in (s.a, s.b)]
        assert len(arcs) == 2                                    # one bench arc per ramp
        assert all(s.grade_pct == 0.0 for s in arcs)
        assert all(s.length_m >= 1.0 for s in arcs)


def test_switchback_too_tight_raises_by_name():
    # circle 260 m, 13 benches (step_in 15.6): floor radius 57 m still passes the floor gate,
    # but the deepest leg needs 150 m of run on a 65 m wall -> sweep 2.3 rad > 1.9 bound
    with pytest.raises(PitGeometryError, match="sweeps"):
        build_open_pit(_design(shape=_circle(260.0), ramp_style="switchback",
                               n_benches=13, ramp_grade_pct=8.0))
