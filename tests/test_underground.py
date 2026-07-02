"""U10 acceptance: underground geometry hand-checks (decline grade, bays -> zones, drift
capacity-1 zones), the three flow modes end-to-end, ORE-PASS CONSERVATION (tipped == chuted +
inventory, every run), shaft-bin conservation, 30 seeds all valid, batch diversity, round-trip."""
import pytest

from minehaulsim.des.dispatch import MinQueuePolicy
from minehaulsim.geometry.underground import (DriftSpec, LevelSpec, OrePassSpec,
                                              UndergroundDesign, UndergroundGeometryError,
                                              build_underground)
from minehaulsim.io import validate_cyclelog, write_cyclelog
from minehaulsim.scenarios import (MineSpec, UndergroundParams, diversity_signature,
                                   generate_underground, generate_underground_batch,
                                   validate_spec)

NEXT = {"load": "haul", "haul": "dump", "dump": "return", "return": "load"}


def _design(**kw) -> UndergroundDesign:
    n = kw.pop("n_levels", 4)
    base = dict(
        n_levels=n, first_level_depth_m=80.0, level_spacing_m=40.0,
        decline_style="spiral", decline_grade_pct=14.3, spiral_radius_m=32.0,
        passing_bay_spacing_m=250.0,
        levels=tuple(LevelSpec(drifts=(DriftSpec(150.0, 2, 0.8),)) for _ in range(n)),
        ore_passes=(OrePassSpec(top_level=0, bottom_level=n - 2, capacity_t=300.0,
                                azimuth_rad=2.2),),
        flow_mode="lhd_orepass_truck")
    base.update(kw)
    return UndergroundDesign(**base)


# ---- geometry hand-checks ----

def test_decline_reaches_every_level_at_exactly_its_elevation():
    d = _design()
    geo = build_underground(d)
    net = geo.network
    for lvl in range(d.n_levels):
        node = net.nodes[geo.access_nodes[lvl]]
        assert node.pos[2] == pytest.approx(d.level_z(lvl), abs=1e-6)
    # every decline segment carries exactly the design grade, single-lane, zoned
    decline_segs = [s for s in net.segments.values() if abs(s.grade_pct) > 1e-9]
    assert decline_segs
    for s in decline_segs:
        assert abs(s.grade_pct) == pytest.approx(d.decline_grade_pct)
        assert s.width_class == 1 and s.zone_id is not None
    assert net.validate() == []


def test_passing_bays_bound_every_zone_span():
    d = _design(n_levels=8, passing_bay_spacing_m=200.0)
    geo = build_underground(d)
    for z in geo.zones.values():
        total = sum(geo.network.segments[sid].length_m for sid in z.segment_ids)
        assert total < 450.0


def test_drift_zones_are_single_vehicle():
    geo = build_underground(_design())
    drift_zones = [z for z in geo.zones.values() if z.max_in_zone == 1]
    assert len(drift_zones) == 4                          # one drift per level in the fixture
    # drawpoints fan off the stubs and are reachable faces
    for lvl, dps in geo.drawpoints.items():
        assert len(dps) == 2


def test_zigzag_turns_are_capacity_one_junctions():
    d = _design(decline_style="zigzag", n_levels=6)
    geo = build_underground(d)
    turn_junctions = [j for j in geo.junctions.values() if j.capacity == 1]
    assert turn_junctions                                 # a zigzag decline has interior turns


def test_bad_designs_raise_by_name():
    with pytest.raises(UndergroundGeometryError, match="haulage"):
        _design(ore_passes=(OrePassSpec(0, 3, 300.0, 1.0),))   # span reaches the haulage level
    with pytest.raises(UndergroundGeometryError, match="truck_shaft"):
        _design(flow_mode="truck_shaft", shaft=False)


# ---- the three flow modes, end-to-end ----

def _run(spec: MineSpec, minutes: float = 90.0):
    return spec.run(MinQueuePolicy(), seed=5, until_s=minutes * 60.0)


def test_lhd_orepass_flow_conserves_material_and_fills_the_cyclelog(tmp_path):
    spec = generate_underground(UndergroundParams(flow_mode="lhd_orepass_truck"), seed=2)
    assert spec.lhds and spec.materials["ore_passes"]
    res = _run(spec)
    assert res.tonnes > 0 and res.cycles >= 1
    # CONSERVATION: every pass balances exactly
    for key, m in res.materials.items():
        if key.startswith("pass_"):
            assert m["tipped_t"] == pytest.approx(m["chuted_t"] + m["inventory_t"], abs=1e-6)
    total_chuted = sum(m["chuted_t"] for k, m in res.materials.items() if k.startswith("pass_"))
    hauled = sum(e["payload_t"] for e in res.events if e["event"] == "haul")
    # chutes drew EXACTLY what trucks hauled + what sat mid-loading at cutoff (in-flight term)
    assert total_chuted == pytest.approx(hauled + res.materials["chute_in_flight_t"], abs=1e-6)
    # the export passes the consumer contract
    p = tmp_path / "ug.csv"
    write_cyclelog(res.events, p)
    assert validate_cyclelog(p).ok


def test_truck_shaft_flow_dumps_at_the_bin_and_hoisting_conserves():
    spec = generate_underground(UndergroundParams(flow_mode="truck_shaft"), seed=3)
    rt = spec.to_runtime()
    assert rt.shaft_bin is not None and list(spec.dumps) == [rt.shaft_bin.node]
    res = _run(spec)
    assert res.tonnes > 0
    bin_sum = res.materials["shaft_bin"]
    assert bin_sum["hoisted_t"] + bin_sum["bin_level_t"] == pytest.approx(res.tonnes, abs=1e-6)


def test_truck_direct_flow_loads_at_stubs_with_no_lhd_agents():
    spec = generate_underground(UndergroundParams(flow_mode="truck_direct"), seed=4)
    assert not spec.lhds and "ore_passes" not in spec.materials
    assert all(x["loader_class"].endswith("_LOADING") for x in spec.loaders)
    res = _run(spec)
    assert res.tonnes > 0 and res.cycles >= 1
    # per-truck cyclelog legality holds underground too
    by_truck: dict[int, list[dict]] = {}
    for e in res.events:
        by_truck.setdefault(e["truck_id"], []).append(e)
    for tid, evs in by_truck.items():
        state = "return"
        for e in evs:
            assert e["event"] == NEXT[state], f"truck {tid}: {state} -> {e['event']}"
            state = e["event"]


def test_empty_pass_parks_trucks_until_lhds_deliver():
    """A tiny pass + slow start: the FIRST truck load must wait for LHD tips — verified by the
    chute's first 'load' event happening AFTER the shift start (inventory starts at zero)."""
    spec = generate_underground(UndergroundParams(flow_mode="lhd_orepass_truck"), seed=2)
    res = _run(spec, minutes=30.0)
    first_load = min(e["t"] for e in res.events if e["event"] == "load")
    assert first_load > 0.0                               # zero inventory cannot load at t=0


def test_zone_policy_ordering_loaded_priority_vs_lockout():
    """Blueprint U10 verify (Queen's 2016 qualitative result): on a decline with opposing
    loaded/empty traffic, loaded_priority arbitration moves AT LEAST as many tonnes as strict
    lockout — priority to climbing loaded trucks cannot lose throughput on the reference spec."""
    import dataclasses
    spec = generate_underground(
        UndergroundParams(flow_mode="truck_direct", n_levels=6, decline_style="spiral",
                          zone_policy="lockout", target_match_factor=1.2), seed=8)

    def with_policy(policy: str) -> MineSpec:
        zones = tuple({**z, "policy": policy} for z in spec.zones)
        return dataclasses.replace(spec, zones=zones)

    res_lp = with_policy("loaded_priority").run(MinQueuePolicy(), seed=6, until_s=4 * 3600.0)
    res_lo = with_policy("lockout").run(MinQueuePolicy(), seed=6, until_s=4 * 3600.0)
    assert res_lp.tonnes > 0 and res_lo.tonnes > 0
    assert res_lp.tonnes >= res_lo.tonnes


# ---- generator acceptance ----

@pytest.mark.parametrize("seed", range(30))
def test_thirty_seeds_all_generate_valid_underground(seed):
    spec = generate_underground(seed=seed)
    report = validate_spec(spec, smoke=False)
    assert report.ok, report.failing()
    assert spec.params["n_levels"] >= 3
    assert len(spec.trucks) >= 2


def test_underground_batch_diversity():
    specs = generate_underground_batch(8, seed=5)
    sigs = [diversity_signature(s) for s in specs]
    assert len(set(sigs)) == 8
    assert len({s.params["flow_mode"] for s in specs}) >= 2


def test_underground_spec_round_trip_reruns_identically(tmp_path):
    spec = generate_underground(UndergroundParams(flow_mode="lhd_orepass_truck"), seed=2)
    p = tmp_path / "ug.minespec.json"
    spec.to_json(p)
    back = MineSpec.from_json(p)
    assert back == spec
    a = _run(spec, minutes=45.0)
    b = _run(back, minutes=45.0)
    assert a.events == b.events and a.materials == b.materials


def test_minetopo_payload_in_spec():
    spec = generate_underground(seed=2)
    assert spec.topo["schema"] == "minehaulsim.minetopo/v1"
    assert len(spec.topo["levels"]) == spec.params["n_levels"]
    assert len(spec.topo["decline"]) > 10
