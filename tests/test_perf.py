"""U11 performance floor: the deterministic engine must sustain >= 20k executed events per
wall-second on the reference scenario, WITH traffic on (slots + zones + no-overtake). Measured
on the same preset the demo uses so the number is reproducible anywhere."""
import time

from minehaulsim.des.dispatch import MinQueuePolicy
from minehaulsim.scenarios import load_preset

PERF_FLOOR_EVENTS_PER_S = 20_000


def test_engine_sustains_the_events_per_second_floor():
    spec = load_preset("starter_pit")
    spec.run(MinQueuePolicy(), seed=7, until_s=1800.0)          # warm caches (routing, speeds)
    t0 = time.perf_counter()
    res = spec.run(MinQueuePolicy(), seed=7, until_s=8 * 3600.0)
    dt = time.perf_counter() - t0
    assert res.events_executed > 5_000                          # a real shift, not a stub
    assert res.events_executed / dt >= PERF_FLOOR_EVENTS_PER_S, (
        f"{res.events_executed / dt:.0f} ev/s < {PERF_FLOOR_EVENTS_PER_S}")
