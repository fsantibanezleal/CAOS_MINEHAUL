"""Engine throughput benchmark: the U11 performance reference.

Runs the starter_pit preset (traffic ON) and a scaled 200-truck synthetic scenario, reporting
executed events per wall-second. The CI floor test (tests/test_perf.py) asserts the small
reference sustains >= 20_000 events/s; the 200-truck figure is recorded in docs.

Usage: python scripts/bench_engine.py [--shift-min 480]
"""
from __future__ import annotations

import argparse
import time

from minehaulsim.des.dispatch import MinQueuePolicy
from minehaulsim.scenarios import OpenPitParams, generate_open_pit, load_preset


def bench(spec, minutes: float, label: str) -> float:
    t0 = time.perf_counter()
    res = spec.run(MinQueuePolicy(), seed=7, until_s=minutes * 60.0)
    dt = time.perf_counter() - t0
    eps = res.events_executed / dt
    print(f"{label:>24}: {res.events_executed:>8} events in {dt:6.2f} s -> {eps:>9.0f} ev/s "
          f"({res.tonnes:.0f} t, {res.cycles} cycles)")
    return eps


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shift-min", type=float, default=480.0)
    args = ap.parse_args()
    bench(load_preset("starter_pit"), args.shift_min, "starter_pit")
    big = generate_open_pit(OpenPitParams(n_benches=14, n_shovels=8, n_crushers=2,
                                          n_waste_dumps=3, target_match_factor=1.5), seed=99)
    bench(big, args.shift_min, f"big pit ({len(big.trucks)} trucks)")


if __name__ == "__main__":
    main()
