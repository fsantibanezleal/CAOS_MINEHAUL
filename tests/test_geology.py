"""U15 geology attachment (oreblocks bridge): determinism, bench anchoring, spec integrity."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("oreblocks", reason="the [geology] extra is not installed")

from minehaulsim.scenarios import MineSpec, attach_geology, generate_open_pit  # noqa: E402


@pytest.fixture(scope="module")
def geo_spec() -> MineSpec:
    return attach_geology(generate_open_pit(seed=11), archetype="porphyry")


def test_deterministic_given_spec_seed(geo_spec: MineSpec):
    again = attach_geology(generate_open_pit(seed=11), archetype="porphyry")
    assert geo_spec.to_json() == again.to_json()
    other = attach_geology(generate_open_pit(seed=11), archetype="porphyry", seed=99)
    assert other.to_json() != geo_spec.to_json()


def test_every_loader_gets_its_bench_geology(geo_spec: MineSpec):
    bench_of = {int(k): int(v) for k, v in geo_spec.topo["shovelBench"].items()}
    nb = int(geo_spec.topo["nBenches"])
    for x in geo_spec.loaders:
        assert x["face_bench"] == bench_of[x["node_id"]]
        assert 1 <= x["face_bench"] <= nb
        assert 0.0 <= x["face_grade"] <= 1.0
        assert 0.0 <= x["face_ore_fraction"] <= 1.0
        assert x["face_level_tonnes"] >= 0.0


def test_materials_geology_block(geo_spec: MineSpec):
    g = geo_spec.materials["geology"]
    assert g["schema"] == "minehaulsim.geology/v1"
    assert g["archetype"] == "porphyry"
    assert g["stamped_pit_value"] > 0
    assert g["stamped_n_in_pit"] > 0
    assert g["grid"]["nz"] == int(geo_spec.topo["nBenches"])
    assert 0 < g["cutoff_grade"] < 0.01
    # a porphyry pit must expose ore somewhere: at least one level has ore_fraction > 0
    assert any(v["ore_fraction"] > 0 for v in g["per_level"].values())


def test_spec_roundtrip_and_run_still_work(geo_spec: MineSpec):
    j = geo_spec.to_json()
    back = MineSpec.from_json(j)
    assert back.to_json() == j
    assert back.materials["geology"]["archetype"] == "porphyry"
    # LoaderSpec ignores the extra face_* keys: the sim runs unchanged (backward compatible)
    res = back.run(until_s=1200.0, fast_mode=True)
    assert res.tonnes >= 0
    # canonical JSON stays ASCII + LF (determinism hash contract)
    assert "\r" not in j
    json.loads(j)


def test_underground_rejected():
    from minehaulsim.scenarios import generate_underground
    ug = generate_underground(seed=3)
    with pytest.raises(ValueError, match="openpit"):
        attach_geology(ug)
