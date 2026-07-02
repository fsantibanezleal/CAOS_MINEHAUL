"""U11 acceptance: failure processes are opt-in, deterministic, and MOVE the KPIs the right way —
breakdowns and loader downtime cost tonnes; a closure window parks trucks and hauling RESUMES
when it reopens; downtime accounting lands in the result."""
import pytest

from minehaulsim.des.dispatch import MinQueuePolicy
from minehaulsim.des.failures import FailureConfig
from minehaulsim.scenarios import OpenPitParams, generate_open_pit

QUICK = OpenPitParams(n_benches=7, n_shovels=2, n_crushers=1, n_waste_dumps=1,
                      stockpile=False, n_surface_junctions=1)


@pytest.fixture(scope="module")
def spec():
    return generate_open_pit(QUICK, seed=7)


def _run(spec, failures=None, minutes=240.0):
    return spec.run(MinQueuePolicy(), seed=5, until_s=minutes * 60.0, failures=failures)


def test_default_runs_are_failure_free(spec):
    res = _run(spec)
    assert res.downtime == {}


def test_truck_breakdowns_cost_tonnes_and_are_accounted(spec):
    base = _run(spec)
    broken = _run(spec, FailureConfig(truck_mtbf_h=0.4, loader_mtbf_h=1e9))
    assert broken.downtime["truck_s"]                     # somebody actually broke down
    assert sum(broken.downtime["truck_s"].values()) > 0
    assert broken.tonnes < base.tonnes                    # breakdowns cost real production


def test_loader_downtime_costs_tonnes(spec):
    base = _run(spec)
    down = _run(spec, FailureConfig(truck_mtbf_h=1e9, loader_mtbf_h=0.3))
    assert down.downtime["loader_s"]
    assert down.tonnes < base.tonnes


def test_failures_are_deterministic(spec):
    cfg = FailureConfig(truck_mtbf_h=0.5, loader_mtbf_h=2.0)
    a = _run(spec, cfg)
    b = _run(spec, cfg)
    assert a.events == b.events and a.downtime == b.downtime


def test_closure_window_pauses_hauling_then_resumes(spec):
    """Close EVERY ramp segment for minutes 60..120: dumps stop arriving during the window
    (in-flight trips drain), and hauling RESUMES after it reopens (the closure-retry loop)."""
    from minehaulsim.network.graph import RoadNetwork
    net = RoadNetwork.from_dict(spec.network)
    ramp_ids = tuple(s.id for s in net.segments.values() if abs(s.grade_pct) > 1e-9)
    cfg = FailureConfig(truck_mtbf_h=1e9, loader_mtbf_h=1e9,
                        closures=((3600.0, 3600.0, ramp_ids),))
    res = _run(spec, cfg, minutes=300.0)
    dumps_before = [e["t"] for e in res.events if e["event"] == "dump" and e["t"] < 3600.0]
    dumps_after = [e["t"] for e in res.events if e["event"] == "dump" and e["t"] > 7200.0]
    assert dumps_before and dumps_after                   # it hauled, paused, and RESUMED
    # nothing can COMPLETE a pit->crusher trip that started inside the window: no dump events
    # in (window start + drain margin, window end)
    in_window = [t for e in res.events if e["event"] == "dump"
                 for t in [e["t"]] if 3600.0 + 900.0 < t < 7200.0]
    assert not in_window
    base = _run(spec, minutes=300.0)
    assert res.tonnes < base.tonnes
