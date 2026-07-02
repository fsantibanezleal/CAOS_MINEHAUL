"""Failure & disturbance processes (blueprint 7.5) — OFF by default, opt-in per run.

Three orthogonal disturbance channels, all drawing ONLY from named RngManager streams:

- **Truck breakdowns**: per-truck exponential time-between-failures (mean `truck_mtbf_h`),
  lognormal repair (mean `truck_repair_mean_s`). A truck failing mid-leg FINISHES its current
  segment, then parks at the next node for the repair duration (v1 semantics: no mid-segment
  blocking — documented; the segment-blocking variant is a later axis).
- **Loader downtime**: per-loader exponential TBF + lognormal repair; a down loader refuses
  service (its queue holds; dispatch sees `est_free_s` grow through MineView).
- **Segment closure windows**: scheduled (start_s, duration_s, segment_ids) maintenance closures;
  the router sees them through the same `closed` frozenset mechanism the planning overlay uses.

Implementation contract: the sim asks `FailureState.next_truck_failure(tid, now)` AFTER each
completed leg (a failure never interrupts an event in flight) — deterministic, event-count
bounded, no polling.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class FailureConfig:
    truck_mtbf_h: float = 40.0
    truck_repair_mean_s: float = 45 * 60.0
    truck_repair_cv: float = 0.6
    loader_mtbf_h: float = 60.0
    loader_repair_mean_s: float = 30 * 60.0
    loader_repair_cv: float = 0.5
    closures: tuple[tuple[float, float, tuple[int, ...]], ...] = ()
    #          (start_s, duration_s, segment_ids)


@dataclass
class FailureState:
    """Per-run failure clocks. Pre-draws each unit's next failure time from its OWN stream so
    the sequence is independent of traffic interleaving (determinism under refactors)."""
    config: FailureConfig
    rng_truck: np.random.Generator
    rng_loader: np.random.Generator
    next_truck_fail_s: dict[int, float] = field(default_factory=dict)
    next_loader_fail_s: dict[int, float] = field(default_factory=dict)
    truck_downtime_s: dict[int, float] = field(default_factory=dict)
    loader_downtime_s: dict[int, float] = field(default_factory=dict)

    def init_truck(self, tid: int) -> None:
        self.next_truck_fail_s[tid] = float(
            self.rng_truck.exponential(self.config.truck_mtbf_h * 3600.0))
        self.truck_downtime_s[tid] = 0.0

    def init_loader(self, nid: int) -> None:
        self.next_loader_fail_s[nid] = float(
            self.rng_loader.exponential(self.config.loader_mtbf_h * 3600.0))
        self.loader_downtime_s[nid] = 0.0

    def truck_due(self, tid: int, now: float) -> bool:
        return now >= self.next_truck_fail_s.get(tid, float("inf"))

    def truck_repair_s(self, tid: int, now: float) -> float:
        cv = self.config.truck_repair_cv
        rep = float(self.config.truck_repair_mean_s
                    * self.rng_truck.lognormal(mean=-0.5 * cv * cv, sigma=cv))
        self.next_truck_fail_s[tid] = now + rep + float(
            self.rng_truck.exponential(self.config.truck_mtbf_h * 3600.0))
        self.truck_downtime_s[tid] += rep
        return rep

    def loader_due(self, nid: int, now: float) -> bool:
        return now >= self.next_loader_fail_s.get(nid, float("inf"))

    def loader_repair_s(self, nid: int, now: float) -> float:
        cv = self.config.loader_repair_cv
        rep = float(self.config.loader_repair_mean_s
                    * self.rng_loader.lognormal(mean=-0.5 * cv * cv, sigma=cv))
        self.next_loader_fail_s[nid] = now + rep + float(
            self.rng_loader.exponential(self.config.loader_mtbf_h * 3600.0))
        self.loader_downtime_s[nid] += rep
        return rep

    def closed_segments(self, now: float) -> frozenset[int]:
        out: set[int] = set()
        for start, dur, sids in self.config.closures:
            if start <= now < start + dur:
                out.update(sids)
        return frozenset(out)
