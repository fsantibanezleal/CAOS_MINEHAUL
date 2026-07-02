"""PitState: the ONLY mutable object in the planning layer — mining progression over a frozen
(PitModel, MinePlan), with explicit transitions, a journal, exact conservation, and the network
overlay that lets progression affect routing without mutating the frozen graph.

Conservation contract (tested to 1e-9 absolute):
    mined_t() + sum(remaining) == model.total_tonnes   after EVERY transition.

Depletion legality (in order; the FAILING RULE IS NAMED in the raised error):
    1. tonnes > 0 (ValueError); block exists (KeyError)
    2. block not complete (BlockCompletedError)
    3. phase prerequisites complete           ("phase-requires")
    4. phase active in the CURRENT period     ("phase-not-active")
    5. bench is the phase's current bench     ("bench-order")
    6. block is the bench's current face      ("block-order")

Advance cascade (U-P2, design P4): after accounting, (1) face reposition quantized at
`reposition_step_m` (spur retire + node move + new spur, revision bump); (2) block completion snaps
the face to the next block's start; (3) bench completion retires the spur, records a topo delta
(floor drop) and ACTIVATES the phase's next bench (materialize face + spur); (4) phase completion
is a mark (activation stays lazy through the legality gate). `advance_period()` materializes faces
of newly-active phases; parked faces remain geometry but not permission.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..network.graph import RoadNetwork, Segment
from ..types import XYZ
from .damage import DamageConfig, DamageEffects, SlopeDamageEvent, resolve_damage
from .overlay import PLANNING_NODE_ID_BASE, PLANNING_SEG_ID_BASE, NetworkOverlay
from .phase import MinePlan, Period
from .pit_model import Bench, PitModel
from .zones import SpeedZone, compose_speed_caps

SPUR_SPEED_LIMIT_KMH = 25.0     # bench-floor operating limit
SPUR_RR_PCT = 2.5               # in-pit surface


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
    overlay_changed: bool


@dataclass(frozen=True)
class FaceStatus:
    phase_id: int
    bench_id: int
    block_id: int
    face_pos: XYZ
    face_node: int | None


class PitState:
    def __init__(self, model: PitModel, plan: MinePlan, reposition_step_m: float = 25.0) -> None:
        issues = plan.validate(model)
        if issues:
            raise ValueError("plan invalid: " + "; ".join(issues))
        self.model = model
        self.plan = plan
        self.reposition_step_m = reposition_step_m
        self.period_idx = 0
        self._remaining: dict[int, float] = {b.id: b.tonnes for b in model.blocks}
        self._mined_by_period: dict[int, dict[str, float]] = {0: {"ore": 0.0, "waste": 0.0}}
        self.journal: list[dict[str, Any]] = []
        # ---- overlay bookkeeping (U-P2) ----
        self._next_node_id = PLANNING_NODE_ID_BASE
        self._next_seg_id = PLANNING_SEG_ID_BASE
        self._revision = 0
        self._face_node: dict[int, int] = {}          # bench -> face node id
        self._face_spur: dict[int, int] = {}          # bench -> current spur segment id
        self._face_arc: dict[int, float] = {}         # bench -> last MATERIALIZED arc position
        self._moved: dict[int, tuple[float, float, float]] = {}
        self._added: dict[int, Segment] = {}
        self._retired: set[int] = set()
        self._closed: set[int] = set()                # composed damage closures
        self._caps: dict[int, float] = {}             # composed speed caps (zones + damage)
        self._zones: dict[int, SpeedZone] = {}        # active named zones
        self._damages: dict[int, tuple[SlopeDamageEvent, DamageEffects]] = {}
        self._topo_delta: list[dict[str, Any]] = []
        for pid in self.plan.periods[0].active_phases:
            self._materialize_phase_face(pid)

    # =========================== read API (pure) ===========================
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
        for bid in self.model.phase(phase_id).bench_ids:
            if not self.is_complete(bid, "bench"):
                return bid
        return None

    def current_block_of(self, bench_id: int) -> int | None:
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

    def face_arc(self, bench_id: int) -> float:
        """The depletion frontier's arc position along the bench polyline."""
        be = self.model.bench(bench_id)
        blk_id = self.current_block_of(bench_id)
        if blk_id is None:
            return be.arc_length_m
        blk = self.model.block(blk_id)
        frac = 1.0 - self._remaining[blk_id] / blk.tonnes
        return blk.arc_s0 + frac * (blk.arc_s1 - blk.arc_s0)

    def face_pos(self, bench_id: int) -> XYZ:
        return _arc_point(self.model.bench(bench_id).polyline, self.face_arc(bench_id))

    def active_faces(self) -> tuple[FaceStatus, ...]:
        out: list[FaceStatus] = []
        for blk_id in self.diggable_blocks():
            blk = self.model.block(blk_id)
            ph = next(p for p in self.model.phases if blk.bench_id in p.bench_ids)
            out.append(FaceStatus(ph.id, blk.bench_id, blk_id, self.face_pos(blk.bench_id),
                                  self._face_node.get(blk.bench_id)))
        return tuple(out)

    def overlay(self) -> NetworkOverlay:
        return NetworkOverlay(
            revision=self._revision,
            moved_nodes=tuple(sorted(self._moved.items())),
            added_segments=tuple(sorted(self._added.values(), key=lambda s: s.id)),
            retired_segments=frozenset(self._retired),
            closed_segments=frozenset(self._closed),
            speed_caps=tuple(sorted(self._caps.items())),
        )

    def routing_inputs(self) -> tuple[frozenset[int], dict[int, float]]:
        return self.overlay().routing_inputs()

    def topo_delta(self) -> list[dict[str, Any]]:
        return [dict(d) for d in self._topo_delta]

    # =========================== transitions ===========================
    def deplete(self, block_id: int, tonnes: float) -> DepletionResult:
        if tonnes <= 0:
            raise ValueError(f"tonnes must be > 0, got {tonnes}")
        blk = self.model.block(block_id)
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
        if self._remaining[block_id] <= 1e-9:
            self._remaining[block_id] = 0.0
        bucket = self._mined_by_period.setdefault(self.period_idx, {"ore": 0.0, "waste": 0.0})
        bucket[blk.material] += take

        rev0 = self._revision
        block_done = self._remaining[block_id] == 0.0
        bench_done = self.is_complete(blk.bench_id, "bench")
        if bench_done:
            self._complete_bench(blk.bench_id, ph.id)
        else:
            self._reposition_if_needed(self.model.bench(blk.bench_id))
        phase_done = self.is_complete(ph.id, "phase")
        res = DepletionResult(take, block_done, bench_done, phase_done, self._revision != rev0)
        self.journal.append({"op": "deplete", "period": self.period_idx, "block": block_id,
                             "take": take, "completed": block_done})
        return res

    def advance_period(self) -> None:
        if self.period_idx + 1 >= len(self.plan.periods):
            raise IndexError("advancing past the last period")
        self.period_idx += 1
        self._mined_by_period.setdefault(self.period_idx, {"ore": 0.0, "waste": 0.0})
        for pid in self.period.active_phases:
            self._materialize_phase_face(pid)
        self.journal.append({"op": "advance_period", "to": self.period_idx})

    # =========================== damage + zones (U-P3) ===========================
    def apply_damage(self, event: SlopeDamageEvent, net: RoadNetwork,
                     cfg: DamageConfig = DamageConfig()) -> DamageEffects:
        """Resolve a wall event against the network and compose its effects into routing state."""
        if event.id in self._damages:
            raise ValueError(f"damage event {event.id} already active")
        eff = resolve_damage(self.model, net, event, cfg)
        self._damages[event.id] = (event, eff)
        self._recompose()
        self.journal.append({"op": "apply_damage", "event": event.id,
                             "severity": event.severity.value,
                             "closed": sorted(eff.closed_segments)})
        return eff

    def clear_damage(self, event_id: int) -> None:
        """Drop an event and RECOMPUTE the composed sets from the remaining active set
        (never decrement-in-place: overlapping damages stay correct)."""
        if event_id not in self._damages:
            raise KeyError(f"no active damage event {event_id}")
        del self._damages[event_id]
        self._recompose()
        self.journal.append({"op": "clear_damage", "event": event_id})

    def add_zone(self, zone: SpeedZone) -> None:
        if zone.id in self._zones:
            raise ValueError(f"zone {zone.id} already active")
        self._zones[zone.id] = zone
        self._recompose()
        self.journal.append({"op": "add_zone", "zone": zone.id, "cap": zone.cap_kmh})

    def remove_zone(self, zone_id: int) -> None:
        if zone_id not in self._zones:
            raise KeyError(f"no active zone {zone_id}")
        del self._zones[zone_id]
        self._recompose()
        self.journal.append({"op": "remove_zone", "zone": zone_id})

    def active_damages(self) -> tuple[SlopeDamageEvent, ...]:
        return tuple(ev for ev, _ in self._damages.values())

    def _recompose(self) -> None:
        """Rebuild closures + caps from the FULL active set (zones + all damages)."""
        self._closed = set()
        derations: dict[int, float] = {}
        for _, eff in self._damages.values():
            self._closed |= eff.closed_segments
            for sid, cap in eff.derated:
                cur = derations.get(sid)
                derations[sid] = cap if cur is None else min(cur, cap)
        self._caps = compose_speed_caps(self._zones.values(), extra=derations)
        # closures dominate: a closed segment needs no cap entry
        for sid in self._closed:
            self._caps.pop(sid, None)

    # =========================== cascade internals ===========================
    def _materialize_phase_face(self, phase_id: int) -> None:
        bench_id = self.current_bench_of(phase_id)
        if bench_id is None or bench_id in self._face_node:
            return
        self._materialize_face(self.model.bench(bench_id))

    def _materialize_face(self, be: Bench) -> None:
        node_id = self._next_node_id
        self._next_node_id += 1
        arc = self.face_arc(be.id)
        pos = _arc_point(be.polyline, arc)
        self._face_node[be.id] = node_id
        self._face_arc[be.id] = arc
        self._moved[node_id] = (pos.x, pos.y, pos.z)
        self._add_spur(be, node_id, arc)
        self._revision += 1
        self.journal.append({"op": "materialize_face", "bench": be.id, "node": node_id})

    def _add_spur(self, be: Bench, face_node: int, arc: float) -> None:
        seg_id = self._next_seg_id
        self._next_seg_id += 1
        # spur length = distance ALONG the bench polyline from the face to the bench anchor end.
        # v1 convention: the anchor sits at arc 0 of the bench polyline (generators enforce this).
        length = max(1.0, arc)
        pos = _arc_point(be.polyline, arc)
        anchor_pos = _arc_point(be.polyline, 0.0)
        poly = np.array([[pos.x, pos.y, pos.z], [anchor_pos.x, anchor_pos.y, anchor_pos.z]])
        self._added[seg_id] = Segment(
            id=seg_id, a=face_node, b=be.anchor_node, polyline=poly, length_m=length,
            grade_pct=0.0, width_class=2, one_way=False,
            speed_limit_kmh=SPUR_SPEED_LIMIT_KMH, zone_id=None, rolling_resistance_pct=SPUR_RR_PCT)
        self._face_spur[be.id] = seg_id

    def _reposition_if_needed(self, be: Bench) -> None:
        if be.id not in self._face_node:
            return                                    # face not materialized (phase not yet active)
        arc_now = self.face_arc(be.id)
        blk_id = self.current_block_of(be.id)
        blk_boundary = blk_id is not None and abs(self.face_arc(be.id) - self.model.block(blk_id).arc_s0) < 1e-9
        if abs(arc_now - self._face_arc[be.id]) < self.reposition_step_m and not blk_boundary:
            return                                    # sub-step drift: no topology change
        old_spur = self._face_spur.get(be.id)
        if old_spur is not None:
            self._retired.add(old_spur)
        node_id = self._face_node[be.id]
        pos = _arc_point(be.polyline, arc_now)
        self._moved[node_id] = (pos.x, pos.y, pos.z)
        self._face_arc[be.id] = arc_now
        self._add_spur(be, node_id, arc_now)
        self._revision += 1
        self.journal.append({"op": "reposition", "bench": be.id, "arc": arc_now})

    def _complete_bench(self, bench_id: int, phase_id: int) -> None:
        spur = self._face_spur.pop(bench_id, None)
        if spur is not None:
            self._retired.add(spur)
        self._face_node.pop(bench_id, None)
        self._face_arc.pop(bench_id, None)
        be = self.model.bench(bench_id)
        self._topo_delta.append({"bench_id": bench_id, "z": be.z, "height_m": be.height_m,
                                 "event": "bench_mined_out"})
        self._revision += 1
        self.journal.append({"op": "bench_complete", "bench": bench_id})
        nxt = self.current_bench_of(phase_id)
        if nxt is not None:
            self._materialize_face(self.model.bench(nxt))

    # =========================== persistence ===========================
    def to_dict(self) -> dict:
        return {"period_idx": self.period_idx,
                "remaining": {str(k): v for k, v in self._remaining.items()},
                "mined_by_period": {str(k): dict(v) for k, v in self._mined_by_period.items()},
                "journal": list(self.journal),
                "overlay": {
                    "next_node_id": self._next_node_id, "next_seg_id": self._next_seg_id,
                    "revision": self._revision,
                    "face_node": {str(k): v for k, v in self._face_node.items()},
                    "face_spur": {str(k): v for k, v in self._face_spur.items()},
                    "face_arc": {str(k): v for k, v in self._face_arc.items()},
                    "moved": {str(k): list(v) for k, v in self._moved.items()},
                    "added": [s.to_dict() for s in self._added.values()],
                    "retired": sorted(self._retired),
                    "closed": sorted(self._closed),
                    "caps": {str(k): v for k, v in self._caps.items()},
                    "topo_delta": list(self._topo_delta),
                    "zones": [z.to_dict() for z in self._zones.values()],
                    "damages": [{"event": ev.to_dict(),
                                 "closed": sorted(eff.closed_segments),
                                 "derated": [list(p) for p in eff.derated],
                                 "zone": eff.exclusion_zone.to_dict() if eff.exclusion_zone else None}
                                for ev, eff in self._damages.values()],
                }}

    @classmethod
    def from_dict(cls, model: PitModel, plan: MinePlan, d: dict) -> "PitState":
        st = cls(model, plan)
        st.period_idx = int(d["period_idx"])
        st._remaining = {int(k): float(v) for k, v in d["remaining"].items()}
        st._mined_by_period = {int(k): {m: float(t) for m, t in v.items()}
                               for k, v in d["mined_by_period"].items()}
        st.journal = list(d["journal"])
        ov = d["overlay"]
        st._next_node_id = int(ov["next_node_id"])
        st._next_seg_id = int(ov["next_seg_id"])
        st._revision = int(ov["revision"])
        st._face_node = {int(k): int(v) for k, v in ov["face_node"].items()}
        st._face_spur = {int(k): int(v) for k, v in ov["face_spur"].items()}
        st._face_arc = {int(k): float(v) for k, v in ov["face_arc"].items()}
        st._moved = {int(k): tuple(v) for k, v in ov["moved"].items()}  # type: ignore[misc]
        st._added = {s["id"]: Segment.from_dict(s) for s in ov["added"]}
        st._retired = set(ov["retired"])
        st._closed = set(ov["closed"])
        st._caps = {int(k): float(v) for k, v in ov["caps"].items()}
        st._topo_delta = list(ov["topo_delta"])
        st._zones = {z["id"]: SpeedZone.from_dict(z) for z in ov.get("zones", [])}
        for dd in ov.get("damages", []):
            ev = SlopeDamageEvent.from_dict(dd["event"])
            eff = DamageEffects(
                event_id=ev.id, closed_segments=frozenset(dd["closed"]),
                derated=tuple((int(a), float(b)) for a, b in dd["derated"]),
                exclusion_zone=SpeedZone.from_dict(dd["zone"]) if dd.get("zone") else None)
            st._damages[ev.id] = (ev, eff)
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
