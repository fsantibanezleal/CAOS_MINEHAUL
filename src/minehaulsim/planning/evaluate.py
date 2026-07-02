"""Evaluate APIs: put a pit + a plan on the table and PRICE them — summary, reachability,
period-by-period feasibility vs a fleet — all pure functions over frozen inputs + a state snapshot.

Honesty note (documented, tested): feasibility cycle times are FREE-FLOW (loaded route + empty
return + nominal load/dump service) — a LOWER bound with no queueing; the DES gives the honest
number. `plan_feasibility` simulates the ORDER constraints without a DES: within a period, available
tonnes = the depletable prefix (blocks in legal order) across the period's active phases, starting
from the given state (or fresh).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..equipment.catalog import LOADERS, TRUCKS
from ..network.graph import RoadNetwork
from ..network.routing import Router
from .phase import MinePlan
from .pit_model import PitModel
from .state import PitState

NOMINAL_DUMP_S = 60.0
DEFAULT_LOADER = "SHOVEL_45"


@dataclass(frozen=True)
class PitSummary:
    total_t: float
    ore_t: float
    waste_t: float
    strip_ratio: float
    n_benches: int
    n_blocks: int
    n_phases: int
    tonnes_by_bench: tuple[tuple[int, float], ...]
    tonnes_by_phase: tuple[tuple[int, float], ...]
    remaining_t: float | None
    active_faces: tuple[int, ...] | None

    def to_dict(self) -> dict:
        return {"total_t": self.total_t, "ore_t": self.ore_t, "waste_t": self.waste_t,
                "strip_ratio": self.strip_ratio, "n_benches": self.n_benches,
                "n_blocks": self.n_blocks, "n_phases": self.n_phases,
                "tonnes_by_bench": [list(x) for x in self.tonnes_by_bench],
                "tonnes_by_phase": [list(x) for x in self.tonnes_by_phase],
                "remaining_t": self.remaining_t,
                "active_faces": None if self.active_faces is None else list(self.active_faces)}


def pit_summary(model: PitModel, state: PitState | None = None) -> PitSummary:
    remaining = None
    faces = None
    if state is not None:
        remaining = float(sum(state.remaining_t(b.id) for b in model.blocks))
        faces = tuple(f.face_node for f in state.active_faces() if f.face_node is not None)
    return PitSummary(
        total_t=model.total_tonnes, ore_t=model.ore_tonnes, waste_t=model.waste_tonnes,
        strip_ratio=model.strip_ratio, n_benches=len(model.benches), n_blocks=len(model.blocks),
        n_phases=len(model.phases),
        tonnes_by_bench=tuple(sorted(model.tonnes_by_bench.items())),
        tonnes_by_phase=tuple(sorted(model.tonnes_by_phase.items())),
        remaining_t=remaining, active_faces=faces)


@dataclass(frozen=True)
class FaceReach:
    face_node: int
    unit: str
    dump: int | None            # best (fastest loaded) dump, None if unreachable
    loaded_s: float | None
    return_s: float | None

    @property
    def reachable(self) -> bool:
        return self.dump is not None and self.return_s is not None


@dataclass(frozen=True)
class ReachabilityReport:
    faces: tuple[FaceReach, ...]

    @property
    def all_reachable(self) -> bool:
        return all(f.reachable for f in self.faces)

    def to_dict(self) -> dict:
        return {"faces": [{"face_node": f.face_node, "unit": f.unit, "dump": f.dump,
                           "loaded_s": f.loaded_s, "return_s": f.return_s,
                           "reachable": f.reachable} for f in self.faces],
                "all_reachable": self.all_reachable}


def reachability(net: RoadNetwork, router: Router, faces: tuple[int, ...], dumps: tuple[int, ...],
                 fleet: Mapping[str, int],
                 closed: frozenset[int] = frozenset(),
                 speed_caps: Mapping[int, float] | None = None) -> ReachabilityReport:
    """Per (face, truck class): the LOADED route to the best dump AND the empty return must exist."""
    caps = dict(speed_caps or {})
    out: list[FaceReach] = []
    for face in faces:
        for cls_name, count in sorted(fleet.items()):
            if count <= 0:
                continue
            unit = TRUCKS[cls_name]
            best_dump, best_t = None, float("inf")
            for d in dumps:
                r = router.route(face, d, unit, loaded=True, closed=closed, speed_caps=caps)
                if r is not None and r.time_s < best_t:
                    best_dump, best_t = d, r.time_s
            if best_dump is None:
                out.append(FaceReach(face, cls_name, None, None, None))
                continue
            back = router.route(best_dump, face, unit, loaded=False, closed=closed, speed_caps=caps)
            out.append(FaceReach(face, cls_name, best_dump, best_t,
                                 None if back is None else back.time_s))
    return ReachabilityReport(faces=tuple(out))


@dataclass(frozen=True)
class PeriodCheck:
    period: int
    available_ore_t: float
    available_waste_t: float
    shortfall_ore_t: float
    shortfall_waste_t: float
    unreachable: tuple[tuple[int, str], ...]
    est_cycle_s: tuple[tuple[int, float], ...]
    fleet_capacity_t: float
    feasible: bool

    def to_dict(self) -> dict:
        return {"period": self.period, "available_ore_t": self.available_ore_t,
                "available_waste_t": self.available_waste_t,
                "shortfall_ore_t": self.shortfall_ore_t,
                "shortfall_waste_t": self.shortfall_waste_t,
                "unreachable": [list(x) for x in self.unreachable],
                "est_cycle_s": [list(x) for x in self.est_cycle_s],
                "fleet_capacity_t": self.fleet_capacity_t, "feasible": self.feasible}


@dataclass(frozen=True)
class FeasibilityReport:
    plan_id: str
    checks: tuple[PeriodCheck, ...]
    feasible: bool

    def to_dict(self) -> dict:
        return {"plan_id": self.plan_id, "checks": [c.to_dict() for c in self.checks],
                "feasible": self.feasible}


def plan_feasibility(model: PitModel, plan: MinePlan, net: RoadNetwork, router: Router,
                     fleet: Mapping[str, int], dumps: tuple[int, ...],
                     state: PitState | None = None,
                     loader: str = DEFAULT_LOADER) -> FeasibilityReport:
    """Period-by-period plan pricing WITHOUT a DES (order-legal availability + free-flow cycles)."""
    sim = PitState(model, plan) if state is None else PitState.from_dict(model, plan, state.to_dict())
    ld = LOADERS[loader]
    checks: list[PeriodCheck] = []
    for per in plan.periods:
        # roll the shadow state to this period
        while sim.period_idx < per.index:
            sim.advance_period()
        # availability: the depletable prefix in legal order across the period's active phases
        avail = {"ore": 0.0, "waste": 0.0}
        probe = PitState.from_dict(model, plan, sim.to_dict())
        while True:
            targets = probe.diggable_blocks()
            if not targets:
                break
            blk_id = targets[0]
            blk = model.block(blk_id)
            take = probe.remaining_t(blk_id)
            probe.deplete(blk_id, take)
            avail[blk.material] += take
        # reachability + free-flow cycles from the CURRENT faces under the state's overlay
        overlay = sim.overlay()
        eff = overlay.effective_network(net)
        rt = Router(eff)
        closed, caps = overlay.routing_inputs()
        face_nodes = tuple(f.face_node for f in sim.active_faces() if f.face_node is not None)
        reach = reachability(eff, rt, face_nodes, dumps, fleet, closed=closed, speed_caps=caps)
        unreachable = tuple((f.face_node, f.unit) for f in reach.faces if not f.reachable)
        cycles: list[tuple[int, float]] = []
        cap_t = 0.0
        for f in reach.faces:
            if not f.reachable:
                continue
            unit = TRUCKS[f.unit]
            import math
            load_s = math.ceil(unit.payload_mean_t / ld.pass_t) * ld.pass_time_s + ld.spot_time_s
            cyc = f.loaded_s + f.return_s + load_s + NOMINAL_DUMP_S
            cycles.append((f.face_node, cyc))
            cap_t += fleet[f.unit] * unit.payload_mean_t * (per.duration_s / cyc) / max(1, len(face_nodes))
        short_ore = max(0.0, per.target_ore_t - avail["ore"])
        short_waste = max(0.0, per.target_waste_t - avail["waste"])
        feasible = (not unreachable and short_ore == 0.0 and short_waste == 0.0
                    and cap_t >= per.target_ore_t + per.target_waste_t)
        checks.append(PeriodCheck(
            period=per.index, available_ore_t=avail["ore"], available_waste_t=avail["waste"],
            shortfall_ore_t=short_ore, shortfall_waste_t=short_waste, unreachable=unreachable,
            est_cycle_s=tuple(sorted(cycles)), fleet_capacity_t=cap_t, feasible=feasible))
        # actually consume this period's availability so later periods see depleted state
        while True:
            targets = sim.diggable_blocks()
            if not targets:
                break
            sim.deplete(targets[0], sim.remaining_t(targets[0]))
    return FeasibilityReport(plan_id=plan.id, checks=tuple(checks),
                             feasible=all(c.feasible for c in checks))
