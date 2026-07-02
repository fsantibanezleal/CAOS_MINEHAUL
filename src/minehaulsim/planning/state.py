"""PitState: the ONLY mutable object in the planning layer — mining progression over a frozen
(PitModel, MinePlan), with explicit transitions, a journal, and exact conservation.

U-P1 scope: legality gates + depletion accounting + period accounting + face positions + journal +
snapshot round-trip. Network effects (overlay/spur re-anchoring, U-P2), damage/zones (U-P3) build on
this without changing these invariants.

The conservation contract (tested to 1e-9 absolute, tonnes scale):
    mined_t() + sum(remaining over all blocks) == model.total_tonnes   after EVERY transition.

Depletion legality (checked in order; the FAILING RULE IS NAMED in the raised error):
    1. tonnes > 0 (ValueError); block exists (KeyError)
    2. block not complete (BlockCompletedError)
    3. phase prerequisites complete           ("phase-requires")
    4. phase active in the CURRENT period     ("phase-not-active")
    5. bench is the phase's current bench     ("bench-order")
    6. block is the bench's current face      ("block-order")
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..types import XYZ
from .phase import MinePlan, Period
from .pit_model import PitModel


class PlanOrderError(RuntimeError):
    """A deplete() that violates plan legality; the message names the failing rule."""


class BlockCompletedError(RuntimeError):
    """Depleting a block that is already exhausted."""


@dataclass(frozen=True)
class DepletionResult:
    taken_t: float
    block_completed: bool
    bench_completed: bool
    phase_completed: bool


@dataclass(frozen=True)
class FaceStatus:
    phase_id: int
    bench_id: int
    block_id: int
    face_pos: XYZ


class PitState:
    def __init__(self, model: PitModel, plan: MinePlan) -> None:
        issues = plan.validate(model)
        if issues:
            raise ValueError("plan invalid: " + "; ".join(issues))
        self.model = model
        self.plan = plan
        self.period_idx = 0
        self._remaining: dict[int, float] = {b.id: b.tonnes for b in model.blocks}
        self._mined_by_period: dict[int, dict[str, float]] = {0: {"ore": 0.0, "waste": 0.0}}
        self.journal: list[dict[str, Any]] = []

    # ---- read API (pure) ----
    @property
    def period(self) -> Period:
        return self.plan.periods[self.period_idx]

    def remaining_t(self, block_id: int) -> float:
        return self._remaining[block_id]

    def mined_t(self) -> float:
        return sum(v for per in self._mined_by_period.values() for v in per.values())

    def mined_by_period(self) -> dict[int, dict[str, float]]:
        return {k: dict(v) for k, v in self._mined_by_period.items()}

    def is_complete(self, obj_id: int, kind: str) -> bool:
        if kind == "block":
            return self._remaining[obj_id] <= 0.0
        if kind == "bench":
            return all(self._remaining[b] <= 0.0 for b in self.model.bench(obj_id).block_ids)
        if kind == "phase":
            return all(self.is_complete(b, "bench") for b in self.model.phase(obj_id).bench_ids)
        raise ValueError(f"unknown kind {kind!r}")

    def current_bench_of(self, phase_id: int) -> int | None:
        """The phase's active bench: first bench in top-down order not yet complete."""
        for bid in self.model.phase(phase_id).bench_ids:
            if not self.is_complete(bid, "bench"):
                return bid
        return None

    def current_block_of(self, bench_id: int) -> int | None:
        """The bench's face: first block in seq order not yet complete."""
        for blk in self.model.bench(bench_id).block_ids:
            if self._remaining[blk] > 0.0:
                return blk
        return None

    def diggable_blocks(self) -> tuple[int, ...]:
        out: list[int] = []
        for pid in self.period.active_phases:
            ph = self.model.phase(pid)
            if any(not self.is_complete(r, "phase") for r in ph.requires):
                continue
            bench = self.current_bench_of(pid)
            if bench is None:
                continue
            blk = self.current_block_of(bench)
            if blk is not None:
                out.append(blk)
        return tuple(sorted(out))

    def face_pos(self, bench_id: int) -> XYZ:
        """The face position: arc-interpolated on the bench polyline at the depletion frontier.

        The frontier arc = block.arc_s0 + consumed_fraction * (arc_s1 - arc_s0) of the CURRENT
        block (or the bench end when complete)."""
        be = self.model.bench(bench_id)
        blk_id = self.current_block_of(bench_id)
        if blk_id is None:
            s = be.arc_length_m
        else:
            blk = self.model.block(blk_id)
            frac = 1.0 - self._remaining[blk_id] / blk.tonnes
            s = blk.arc_s0 + frac * (blk.arc_s1 - blk.arc_s0)
        return _arc_point(be.polyline, s)

    def active_faces(self) -> tuple[FaceStatus, ...]:
        out: list[FaceStatus] = []
        for blk_id in self.diggable_blocks():
            blk = self.model.block(blk_id)
            ph = next(p for p in self.model.phases if blk.bench_id in p.bench_ids)
            out.append(FaceStatus(ph.id, blk.bench_id, blk_id, self.face_pos(blk.bench_id)))
        return tuple(out)

    # ---- transitions ----
    def deplete(self, block_id: int, tonnes: float) -> DepletionResult:
        if tonnes <= 0:
            raise ValueError(f"tonnes must be > 0, got {tonnes}")
        blk = self.model.block(block_id)          # KeyError if unknown
        if self._remaining[block_id] <= 0.0:
            raise BlockCompletedError(f"block {block_id} already complete")
        ph = next(p for p in self.model.phases if blk.bench_id in p.bench_ids)
        for r in ph.requires:
            if not self.is_complete(r, "phase"):
                raise PlanOrderError(f"phase-requires: phase {ph.id} requires incomplete phase {r}")
        if ph.id not in self.period.active_phases:
            raise PlanOrderError(f"phase-not-active: phase {ph.id} not active in period {self.period_idx}")
        if self.current_bench_of(ph.id) != blk.bench_id:
            raise PlanOrderError(f"bench-order: bench {blk.bench_id} is not phase {ph.id}'s current bench")
        if self.current_block_of(blk.bench_id) != block_id:
            raise PlanOrderError(f"block-order: block {block_id} is not bench {blk.bench_id}'s current face")

        take = min(tonnes, self._remaining[block_id])
        self._remaining[block_id] -= take
        bucket = self._mined_by_period.setdefault(self.period_idx, {"ore": 0.0, "waste": 0.0})
        bucket[blk.material] += take
        res = DepletionResult(
            taken_t=take,
            block_completed=self._remaining[block_id] <= 0.0,
            bench_completed=self.is_complete(blk.bench_id, "bench"),
            phase_completed=self.is_complete(ph.id, "phase"),
        )
        self.journal.append({"op": "deplete", "period": self.period_idx, "block": block_id,
                             "take": take, "completed": res.block_completed})
        return res

    def advance_period(self) -> None:
        if self.period_idx + 1 >= len(self.plan.periods):
            raise IndexError("advancing past the last period")
        self.period_idx += 1
        self._mined_by_period.setdefault(self.period_idx, {"ore": 0.0, "waste": 0.0})
        self.journal.append({"op": "advance_period", "to": self.period_idx})

    # ---- persistence (the resume contract) ----
    def to_dict(self) -> dict:
        return {"period_idx": self.period_idx,
                "remaining": {str(k): v for k, v in self._remaining.items()},
                "mined_by_period": {str(k): dict(v) for k, v in self._mined_by_period.items()},
                "journal": list(self.journal)}

    @classmethod
    def from_dict(cls, model: PitModel, plan: MinePlan, d: dict) -> "PitState":
        st = cls(model, plan)
        st.period_idx = int(d["period_idx"])
        st._remaining = {int(k): float(v) for k, v in d["remaining"].items()}
        st._mined_by_period = {int(k): {m: float(t) for m, t in v.items()}
                               for k, v in d["mined_by_period"].items()}
        st.journal = list(d["journal"])
        return st


def _arc_point(polyline: np.ndarray, s: float) -> XYZ:
    """Point at arc length s along a polyline (clamped to its ends)."""
    d = np.diff(polyline, axis=0)
    seg_len = np.sqrt((d * d).sum(axis=1))
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    s = float(min(max(s, 0.0), cum[-1]))
    i = int(np.searchsorted(cum, s, side="right") - 1)
    i = min(i, len(seg_len) - 1)
    t = 0.0 if seg_len[i] <= 0 else (s - cum[i]) / seg_len[i]
    p = polyline[i] + t * d[i]
    return XYZ(float(p[0]), float(p[1]), float(p[2]))
