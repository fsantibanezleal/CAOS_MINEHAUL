"""U8 scenario acceptance (blueprint): 60 seeds all valid; batch diversity signatures unique;
MineSpec JSON round-trip re-runs identically; end-to-end byte-determinism of spec AND cyclelog;
each named validity check fails on a crafted violation; presets regenerate and differ."""
import dataclasses
import json

import pytest

from minehaulsim.des.dispatch import MinQueuePolicy
from minehaulsim.io import validate_cyclelog, write_cyclelog
from minehaulsim.scenarios import (GenerationError, MineSpec, OpenPitParams, diversity_signature,
                                   generate_batch, generate_open_pit, load_preset, preset_names,
                                   validate_spec)

QUICK = OpenPitParams(n_benches=7, n_shovels=2, n_crushers=1, n_waste_dumps=1,
                      stockpile=False, n_surface_junctions=1)


# ---- the blueprint acceptance ----

@pytest.mark.parametrize("seed", range(60))
def test_sixty_seeds_all_generate_valid_scenarios(seed):
    spec = generate_open_pit(seed=seed)
    # the generator already gated it; re-assert the cheap structural checks independently
    report = validate_spec(spec, smoke=False)
    assert report.ok, report.failing()
    assert spec.params["n_benches"] >= 6 and len(spec.loaders) >= 2
    assert len(spec.trucks) >= 4


def test_batch_diversity_signatures_unique():
    specs = generate_batch(12, seed=100)
    sigs = [diversity_signature(s) for s in specs]
    assert len(set(sigs)) == 12
    # and the variety is real: more than one ramp style and depth bucket in the batch
    assert len({s.params["ramp_style"] for s in specs}) >= 2
    assert len({s.params["n_benches"] for s in specs}) >= 4


def test_spec_json_round_trip_reruns_identically(tmp_path):
    spec = generate_open_pit(QUICK, seed=7)
    p = tmp_path / "pit7.minespec.json"
    spec.to_json(p)
    back = MineSpec.from_json(p)
    assert back == spec
    a = spec.run(MinQueuePolicy(), seed=3, until_s=3600.0)
    b = back.run(MinQueuePolicy(), seed=3, until_s=3600.0)
    assert a.events == b.events and a.tonnes == b.tonnes


def test_end_to_end_byte_determinism(tmp_path):
    s1 = generate_open_pit(QUICK, seed=21)
    s2 = generate_open_pit(QUICK, seed=21)
    assert s1.to_json() == s2.to_json()                     # same bytes, same spec
    r1 = s1.run(MinQueuePolicy(), seed=5, until_s=3600.0)
    r2 = s2.run(MinQueuePolicy(), seed=5, until_s=3600.0)
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    write_cyclelog(r1.events, pa)
    write_cyclelog(r2.events, pb)
    assert pa.read_bytes() == pb.read_bytes()
    # and the export passes the consumer's own ingestion rules
    rep = validate_cyclelog(pa)
    assert rep.ok, rep.rejected
    # different seed -> structurally different pit (not just different noise)
    s3 = generate_open_pit(QUICK, seed=22)
    assert s3.network != s1.network


def test_generated_cyclelog_ids_honor_the_contract():
    spec = generate_open_pit(QUICK, seed=13)
    assert [x["node_id"] for x in spec.loaders] == list(range(1, len(spec.loaders) + 1))
    assert all(d >= 101 for d in spec.dumps)
    assert [t["truck_id"] for t in spec.trucks] == list(range(1, len(spec.trucks) + 1))
    # topo carries the exact consumer key set
    assert set(spec.topo.keys()) == {"center", "rimRx", "rimRy", "nBenches", "benchHeightM",
                                     "benchWidthM", "faceAngleDeg", "rampWidthM", "shovelBench",
                                     "roads"}
    assert spec.topo["roads"]["schema"] == "minehaulsim.roads/v1"
    assert set(spec.topo["shovelBench"]) == {str(x["node_id"]) for x in spec.loaders}


# ---- each named check fails on a crafted violation ----

def _mutated(spec: MineSpec, **kw) -> MineSpec:
    return dataclasses.replace(spec, **kw)


@pytest.fixture(scope="module")
def base_spec() -> MineSpec:
    return generate_open_pit(QUICK, seed=7)


def test_check_grades_catches_a_hot_segment(base_spec):
    net = json.loads(json.dumps(base_spec.network))
    net["segments"][0]["grade_pct"] = 14.0
    bad = _mutated(base_spec, network=net)
    assert "grades" in validate_spec(bad, smoke=False).failing()


def test_check_contract_catches_bad_ids(base_spec):
    bad = _mutated(base_spec, dumps=(99,) + base_spec.dumps[1:])
    assert "contract_ready" in validate_spec(bad, smoke=False).failing()


def test_check_connectivity_catches_a_severed_pit(base_spec):
    net = json.loads(json.dumps(base_spec.network))
    # drop every ramp segment: faces can no longer reach the surface
    net["segments"] = [s for s in net["segments"] if abs(s["grade_pct"]) < 1e-9]
    bad = _mutated(base_spec, network=net)
    assert "connectivity" in validate_spec(bad, smoke=False).failing()


def test_check_geometry_catches_a_shallow_floor(base_spec):
    params = dict(base_spec.params)
    params["floor_r_min_m"] = 12.0
    bad = _mutated(base_spec, params=params)
    assert "geometry_sane" in validate_spec(bad, smoke=False).failing()


def test_check_throughput_catches_an_absurd_fleet(base_spec):
    trucks = tuple({"truck_id": i + 1, "unit_name": "CAT_793F",
                    "start_loader": base_spec.trucks[0]["start_loader"]} for i in range(200))
    bad = _mutated(base_spec, trucks=trucks)
    assert "throughput_sane" in validate_spec(bad, smoke=False).failing()


def test_generation_error_names_the_failing_checks():
    # deterministic impossibility: a deep pit (cycle >= ~30 min) with target MF 0.1 — the fleet
    # floor (4 trucks) still leaves the static MF below 0.5, so throughput_sane fails every attempt
    with pytest.raises(GenerationError, match="throughput_sane"):
        generate_open_pit(OpenPitParams(n_benches=20, bench_height_m=15.0,
                                        ramp_style="spiral", target_match_factor=0.1),
                          seed=1, max_attempts=3)


# ---- presets ----

def test_presets_regenerate_and_are_structurally_distinct():
    names = preset_names()
    assert names == ["deep_spiral", "starter_pit", "switchback_ridge", "twin_ramp_expansion"]
    specs = {n: load_preset(n) for n in names}
    assert specs["starter_pit"].to_json() == load_preset("starter_pit").to_json()
    sigs = {n: diversity_signature(s) for n, s in specs.items()}
    assert len(set(sigs.values())) == len(names)
    assert specs["deep_spiral"].params["ramp_lanes"] == 1
    assert len(specs["deep_spiral"].zones) > 0                  # zoned single-lane ramp
    assert specs["twin_ramp_expansion"].params["ramp_style"] == "dual_spiral"
    with pytest.raises(KeyError):
        load_preset("nope")
