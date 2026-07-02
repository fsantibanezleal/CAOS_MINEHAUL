"""Phases (pushbacks) and the mine plan: which faces are legally diggable, and when.

A Phase mines its benches TOP-DOWN in `bench_ids` order and may require predecessor phases (the
bench-lag simplification of v1: full completion; the Milawa-style bench-lead lag is a later axis,
documented in docs/what-it-is-and-isnt). A MinePlan is a contiguous sequence of Periods, each
declaring the phases whose faces are DIGGABLE during it plus optional tonnage targets — evaluation
compares achieved vs target; the simulator enforces only LEGALITY, never magically moves tonnes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Phase:
    id: int
    name: str
    bench_ids: tuple[int, ...]          # top-down mining order within the phase
    requires: tuple[int, ...] = ()      # predecessor phase ids (ALL complete before digging here)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "bench_ids": list(self.bench_ids),
                "requires": list(self.requires)}

    @classmethod
    def from_dict(cls, d: dict) -> "Phase":
        return cls(int(d["id"]), str(d["name"]), tuple(int(x) for x in d["bench_ids"]),
                   tuple(int(x) for x in d.get("requires", [])))


@dataclass(frozen=True)
class Period:
    index: int                          # 0-based, contiguous
    duration_s: float
    active_phases: tuple[int, ...]
    target_ore_t: float = 0.0
    target_waste_t: float = 0.0

    def __post_init__(self) -> None:
        if self.duration_s <= 0:
            raise ValueError(f"period {self.index}: duration_s must be > 0")

    def to_dict(self) -> dict:
        return {"index": self.index, "duration_s": self.duration_s,
                "active_phases": list(self.active_phases),
                "target_ore_t": self.target_ore_t, "target_waste_t": self.target_waste_t}

    @classmethod
    def from_dict(cls, d: dict) -> "Period":
        return cls(int(d["index"]), float(d["duration_s"]), tuple(int(x) for x in d["active_phases"]),
                   float(d.get("target_ore_t", 0.0)), float(d.get("target_waste_t", 0.0)))


@dataclass(frozen=True)
class MinePlan:
    id: str
    periods: tuple[Period, ...]

    def __post_init__(self) -> None:
        if not self.periods:
            raise ValueError("plan must have at least one period")
        if [p.index for p in self.periods] != list(range(len(self.periods))):
            raise ValueError("periods must be contiguous 0-based")

    def validate(self, model) -> list[str]:
        """Plan-vs-model issues (empty = valid). Includes the precedence-impossibility flag:
        a phase active in period p whose `requires` closure contains a phase never active in
        any period <= p can never legally dig — the plan is broken by construction."""
        issues: list[str] = []
        known = {ph.id for ph in model.phases}
        for per in self.periods:
            for pid in per.active_phases:
                if pid not in known:
                    issues.append(f"period {per.index}: unknown phase {pid}")
        # precedence impossibility (transitive closure of requires vs activation windows)
        first_active: dict[int, int] = {}
        for per in self.periods:
            for pid in per.active_phases:
                first_active.setdefault(pid, per.index)
        def closure(pid: int, seen: frozenset[int]) -> frozenset[int]:
            if pid in seen or pid not in known:
                return seen
            seen = seen | {pid}
            for r in model.phase(pid).requires:
                seen = closure(r, seen)
            return seen
        for pid, p0 in first_active.items():
            for req in closure(pid, frozenset()) - {pid}:
                if req not in first_active or first_active[req] > p0:
                    issues.append(
                        f"precedence-impossible: phase {pid} active in period {p0} requires phase {req} "
                        f"never active earlier")
        return issues

    def to_dict(self) -> dict:
        return {"id": self.id, "periods": [p.to_dict() for p in self.periods]}

    @classmethod
    def from_dict(cls, d: dict) -> "MinePlan":
        return cls(id=str(d["id"]), periods=tuple(Period.from_dict(x) for x in d["periods"]))
