"""U7 acceptance: cyclelog round-trip (run_shift -> CSV -> the consumer's own validation rules),
byte-determinism of the export, provenance shape, PitTopoSpec ellipse fit against a known ellipse."""
import numpy as np
import pytest

from minehaulsim.des.dispatch import MinQueuePolicy
from minehaulsim.des.sim import run_shift
from minehaulsim.io import (fit_ellipse_axes, validate_cyclelog, write_cyclelog,
                            write_pit_topo_spec, write_provenance)
from tests.test_sim import LOADERS, TRUCKS6, _net


def _shift():
    return run_shift(_net(), LOADERS, [200], TRUCKS6, MinQueuePolicy(), seed=42)


def test_cyclelog_round_trip_passes_the_consumer_contract(tmp_path):
    res = _shift()
    p = tmp_path / "shift.csv"
    n = write_cyclelog(res.events, p)
    assert n == len(res.events)
    rep = validate_cyclelog(p)
    assert rep.ok, rep.rejected
    assert rep.trucks == [1, 2, 3, 4, 5, 6]
    assert rep.shovels == [1, 2] and rep.dumps == [200]
    # first row is re-zeroed; header exact; LF endings
    text = p.read_text(encoding="utf-8")
    lines = text.split("\n")
    assert lines[0] == "t,truck_id,shovel_id,event,payload_t"
    assert lines[1].startswith("0.0,")
    assert "\r" not in text


def test_cyclelog_export_is_byte_deterministic(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    write_cyclelog(_shift().events, a)
    write_cyclelog(_shift().events, b)
    assert a.read_bytes() == b.read_bytes()


def test_validator_rejects_bad_logs(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("t,truck_id,shovel_id,event,payload_t\n0.0,1,1,fly,0.0\n", encoding="utf-8")
    rep = validate_cyclelog(p)
    assert not rep.ok
    # illegal transition
    p2 = tmp_path / "bad2.csv"
    rows = ["t,truck_id,shovel_id,event,payload_t"]
    rows += [f"{i}.0,1,1,load,0.0" for i in range(10)]        # load->load repeatedly
    p2.write_text("\n".join(rows) + "\n", encoding="utf-8")
    rep2 = validate_cyclelog(p2)
    assert not rep2.ok and any("illegal" in r["reason"] for r in rep2.rejected)
    # wrong header
    p3 = tmp_path / "bad3.csv"
    p3.write_text("time,truck,node,event,tons\n", encoding="utf-8")
    assert not validate_cyclelog(p3).ok


def test_provenance_shape(tmp_path):
    prov = write_provenance(tmp_path / "s.provenance.json", sample_id="mhs-pit42-minqueue",
                            name="Pit 42 shift", dispatcher="MinQueuePolicy", sim_time_min=480,
                            scenario_seed=42, sim_seed=7, kind="openpit",
                            spec_summary="12 benches, 4 shovels, 20 trucks")
    assert prov["schema"] == "dispatchlab.cyclelog/v1"
    assert prov["kind"] == "structure-real"
    assert "minehaulsim" in prov["source"]
    assert prov["generator"]["scenario_seed"] == 42
    assert (tmp_path / "s.provenance.json").exists()


def test_pit_topo_spec_ellipse_fit_recovers_known_axes(tmp_path):
    # a perturbed ellipse rim with known axes 400 x 300 around (50, -20)
    th = np.linspace(0, 2 * np.pi, 96, endpoint=False)
    rng = np.random.default_rng(3)
    noise = 1.0 + 0.03 * rng.standard_normal(len(th))
    rim = np.stack([50 + 400 * noise * np.cos(th), -20 + 300 * noise * np.sin(th)], axis=1)
    spec = write_pit_topo_spec(tmp_path / "s.topo.json", center=(50.0, -20.0), rim_xy=rim,
                               n_benches=8, bench_height_m=15.0, bench_width_m=12.0,
                               face_angle_deg=65.0, ramp_width_m=25.0,
                               shovel_bench={1: 4, 2: 6})
    assert spec["rimRx"] == pytest.approx(400.0, rel=0.05)
    assert spec["rimRy"] == pytest.approx(300.0, rel=0.05)
    assert spec["shovelBench"] == {"1": 4, "2": 6}
    # exact DispatchLab key set
    assert set(spec.keys()) == {"center", "rimRx", "rimRy", "nBenches", "benchHeightM",
                                "benchWidthM", "faceAngleDeg", "rampWidthM", "shovelBench"}
    rx, ry = fit_ellipse_axes(rim, (50.0, -20.0))
    assert rx > ry


def test_pit_topo_spec_carries_the_road_network(tmp_path):
    # with a network passed, the topo.json gains the minehaulsim.roads/v1 block so a 3D consumer
    # renders the REAL roads + can mirror the segment traffic model (#28). Without it, unchanged.
    net = _net()
    th = np.linspace(0, 2 * np.pi, 64, endpoint=False)
    rim = np.stack([500 * np.cos(th), 400 * np.sin(th)], axis=1)
    spec = write_pit_topo_spec(tmp_path / "s.topo.json", center=(0.0, 0.0), rim_xy=rim,
                               n_benches=6, bench_height_m=15.0, bench_width_m=12.0,
                               face_angle_deg=65.0, ramp_width_m=25.0, shovel_bench={1: 3, 2: 5},
                               network=net, headway_m=80.0, headway_s=9.0)
    roads = spec["roads"]
    assert roads["schema"] == "minehaulsim.roads/v1"
    assert {n["id"] for n in roads["nodes"]} == {1, 2, 200}
    assert {n["kind"] for n in roads["nodes"]} == {"face", "crusher"}
    assert len(roads["segments"]) == 2
    seg = roads["segments"][0]
    assert set(seg) >= {"id", "a", "b", "polyline", "oneWay", "speedLimitKmh", "zoneId"}
    assert seg["speedLimitKmh"] == 50.0
    assert len(seg["polyline"]) >= 2 and len(seg["polyline"][0]) == 3
    assert roads["traffic"] == {"headwayM": 80.0, "headwayS": 9.0}
    assert (tmp_path / "s.topo.json").exists()   # wrote without a JSON-serialization error


def test_pit_topo_spec_without_network_is_unchanged(tmp_path):
    th = np.linspace(0, 2 * np.pi, 48, endpoint=False)
    rim = np.stack([300 * np.cos(th), 300 * np.sin(th)], axis=1)
    spec = write_pit_topo_spec(tmp_path / "s.topo.json", center=(0.0, 0.0), rim_xy=rim,
                               n_benches=5, bench_height_m=15.0, bench_width_m=12.0,
                               face_angle_deg=65.0, ramp_width_m=25.0, shovel_bench={1: 3})
    assert "roads" not in spec   # backward compatible: no network -> no roads block
