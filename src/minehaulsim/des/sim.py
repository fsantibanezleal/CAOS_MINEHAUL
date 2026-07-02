"""run_shift: the open-pit haul cycle on the constrained network — the layer that joins the engine,
the routing, the equipment, the plan and the dispatch policy.

Cycle (event semantics = cyclelog/v1, each event marks the START of its phase):
    TO_LOADER -> QUEUE_AT_LOADER -> LOADING('load') -> TO_DUMP('haul', payload set)
    -> QUEUE_AT_DUMP -> DUMPING('dump') -> dispatch decision('return') -> TO_LOADER ...

Travel model (U6a): a leg's duration is the ROUTE's free-flow kinematic time from the constrained
Router (closures + speed caps from the PlanContext overlay respected on every quote). Per-segment
slot/zone/junction occupancy (the emergent-bunching tier, resources already built+tested in U5) is
wired in U6b — documented so nobody mistakes free-flow for congested times.

Plan coupling (PlanContext, design P8): loaders at non-diggable faces REFUSE service (the policy
never sees them as serviceable); every completed loading calls on_load(face, payload) so cyclelog
tonnes and model depletion are the SAME number; agents re-quote routes when overlay_revision bumps
(rebuilding Router + effective network), never mid-leg. Without a plan, reserves are infinite.

Determinism: same (spec inputs, policy, seed) => identical event list. All randomness from named
RngManager streams: payload, loadtime, dumptime, policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..equipment.catalog import LOADERS, TRUCKS, LoaderClass, TruckClass
from ..network.graph import RoadNetwork
from ..network.routing import Router
from ..rng import RngManager
from .dispatch import DispatchPolicy, LoaderView, MineView, TruckView
from .engine import Engine
from .resources import QueueResource

NOMINAL_DUMP_MEAN_S = 55.0
DUMP_CV = 0.15


@dataclass(frozen=True)
class LoaderSpec:
    node_id: int
    loader_class: str = "SHOVEL_45"
    n_spots: int = 1


@dataclass(frozen=True)
class TruckSpec:
    truck_id: int
    unit_name: str
    start_loader: int


@dataclass
class ShiftResult:
    events: list[dict] = field(default_factory=list)     # cyclelog/v1 rows (t seconds, floats)
    tonnes: float = 0.0
    cycles: int = 0
    truck_wait_s: float = 0.0
    loader_wait_s: dict[int, float] = field(default_factory=dict)
    events_executed: int = 0


class _Sim:
    def __init__(self, net: RoadNetwork, loaders: list[LoaderSpec], dumps: list[int],
                 trucks: list[TruckSpec], policy: DispatchPolicy, seed: int,
                 plan_context=None, until_s: float = 8 * 3600.0) -> None:
        self.base_net = net
        self.plan = plan_context
        self.engine = Engine()
        self.rng = RngManager(seed)
        self.policy = policy
        self.until_s = until_s
        self.result = ShiftResult()
        self.dumps = list(dumps)
        self.loader_specs = {ls.node_id: ls for ls in loaders}
        self.trucks = {t.truck_id: t for t in trucks}
        self._rebuild_router()
        # resources
        self.loader_q: dict[int, QueueResource] = {
            ls.node_id: QueueResource(engine=self.engine, capacity=ls.n_spots) for ls in loaders}
        self.dump_q: dict[int, QueueResource] = {
            d: QueueResource(engine=self.engine, capacity=1) for d in dumps}
        self.loader_busy_until: dict[int, float] = {ls.node_id: 0.0 for ls in loaders}
        self.inbound: dict[int, int] = {ls.node_id: 0 for ls in loaders}
        self.truck_pos: dict[int, int] = {}              # truck -> current node

    # ---- plan-aware network/router ----
    def _rebuild_router(self) -> None:
        if self.plan is not None:
            self._net = self.plan.effective_network(self.base_net)
            self._revision = self.plan.overlay_revision()
        else:
            self._net = self.base_net
            self._revision = 0
        self.router = Router(self._net)

    def _routing_state(self) -> tuple[frozenset[int], dict[int, float]]:
        if self.plan is None:
            return frozenset(), {}
        if self.plan.overlay_revision() != self._revision:
            self._rebuild_router()
            self._remap_loaders()
        return self.plan.routing_inputs()

    def _remap_loaders(self) -> None:
        """The physical shovel MOVES with the mining front: when a loader's face node is no longer
        an active face (bench completed) and a new face has no loader, migrate the loader (spec +
        queue resource + counters) to the new face node."""
        if self.plan is None:
            return
        active = {f.face_node for f in self.plan.active_faces()}  # type: ignore[attr-defined]
        dead = [n for n in self.loader_specs if n not in active]
        vacant = [n for n in sorted(active) if n not in self.loader_specs]
        for old, new in zip(dead, vacant):
            spec = self.loader_specs.pop(old)
            self.loader_specs[new] = LoaderSpec(new, spec.loader_class, spec.n_spots)
            self.loader_q[new] = self.loader_q.pop(old)
            self.loader_busy_until[new] = self.loader_busy_until.pop(old)
            self.inbound[new] = self.inbound.pop(old)

    def _travel_s(self, truck: TruckSpec, a: int, b: int, loaded: bool) -> float | None:
        closed, caps = self._routing_state()
        unit = TRUCKS[truck.unit_name]
        r = self.router.route(a, b, unit, loaded=loaded, closed=closed, speed_caps=caps)
        return None if r is None else r.time_s

    # ---- views for the policy ----
    def _loader_node_for(self, node_id: int) -> int:
        """The routing target for a loader: with a plan, its face node; else the spec node."""
        if self.plan is None:
            return node_id
        for f in self.plan_active_faces():
            if f[0] == node_id:
                return f[1]
        return node_id

    def plan_active_faces(self) -> list[tuple[int, int]]:
        """[(loader spec node = bench anchor, face node)] pairs for active faces (plan mode)."""
        if self.plan is None:
            return []
        out = []
        for fs in self.plan.active_faces():  # type: ignore[attr-defined]
            # anchor of the face's bench is the loader "home"; face node is the routing target
            out.append((fs.face_node, fs.face_node))
        return out

    def _mine_view(self, truck: TruckSpec, at_node: int) -> MineView:
        self._routing_state()                        # sync router + loader remap ONCE, before iterating
        loaders = []
        etas: dict[tuple[int, int], float] = {}
        for ls in list(self.loader_specs.values()):
            diggable = True
            target = ls.node_id
            if self.plan is not None:
                diggable = self.plan.is_diggable(ls.node_id)
            q = self.loader_q[ls.node_id]
            loaders.append(LoaderView(
                node_id=ls.node_id, queue_len=q.queue_len, inbound=self.inbound[ls.node_id],
                in_service=q.in_service > 0,
                est_free_s=max(0.0, self.loader_busy_until[ls.node_id] - self.engine.now),
                load_mean_s=self._load_mean_s(ls, truck), diggable=diggable))
            t = self._travel_s(truck, at_node, target, loaded=False)
            etas[(truck.truck_id, ls.node_id)] = float("inf") if t is None else t
        return MineView(now=self.engine.now, loaders=tuple(loaders), dumps=tuple(self.dumps),
                        eta_s=etas)

    def _load_mean_s(self, ls: LoaderSpec, truck: TruckSpec) -> float:
        import math
        lc: LoaderClass = LOADERS[ls.loader_class]
        payload = TRUCKS[truck.unit_name].payload_mean_t
        return math.ceil(payload / lc.pass_t) * lc.pass_time_s + lc.spot_time_s

    # ---- the cycle ----
    def start(self) -> None:
        stagger = self.rng.stream("init")
        for tid in sorted(self.trucks):
            t = self.trucks[tid]
            self.truck_pos[tid] = t.start_loader
            self.engine.schedule(float(stagger.uniform(0.0, 60.0)), self._go_load, tid, t.start_loader)

    def _emit(self, t: float, truck: int, node: int, event: str, payload: float) -> None:
        self.result.events.append({"t": t, "truck_id": truck, "shovel_id": node,
                                   "event": event, "payload_t": payload})

    def _go_load(self, tid: int, loader: int) -> None:
        """Truck arrives in the loader's queue area."""
        if loader not in self.loader_specs:
            # the loader MIGRATED to a new face while this truck was inbound: re-dispatch from the
            # truck's previous node (v1 approximation, documented: the travel time was spent)
            prev = self.truck_pos.get(tid, loader)
            self._dispatch_next(tid, prev if prev != loader else loader)
            return
        self.inbound[loader] = max(0, self.inbound[loader] - 1)
        self.truck_pos[tid] = loader
        q = self.loader_q[loader]
        arrive_t = self.engine.now

        def granted() -> None:
            if self.plan is not None and not self.plan.is_diggable(loader):
                # face became non-diggable while queued: release the spot and redispatch empty
                q.release()
                self._dispatch_next(tid, loader)
                return
            self.result.truck_wait_s += self.engine.now - arrive_t
            truck = self.trucks[tid]
            ls = self.loader_specs[loader]
            mean = self._load_mean_s(ls, truck)
            cv = LOADERS[ls.loader_class].time_cv
            lt = self.rng.stream("loadtime")
            load_s = float(mean * lt.lognormal(mean=-0.5 * cv * cv, sigma=cv))
            self._emit(self.engine.now, tid, loader, "load", 0.0)
            self.loader_busy_until[loader] = self.engine.now + load_s
            self.engine.after(load_s, self._loaded, tid, loader)

        q.request(granted)

    def _loaded(self, tid: int, loader: int) -> None:
        truck = self.trucks[tid]
        unit: TruckClass = TRUCKS[truck.unit_name]
        pay_rng = self.rng.stream("payload")
        payload = float(max(0.5 * unit.payload_mean_t,
                            min(400.0, pay_rng.normal(unit.payload_mean_t, unit.payload_sd_t))))
        # QUOTE the outbound route BEFORE depleting: if this load completes the bench, the face
        # spur retires — but the truck physically leaves on the geometry it arrived on (design P4:
        # in-flight legs finish on the old geometry).
        mv = self._mine_view(truck, loader)
        dump = self.policy.next_dump(TruckView(tid, truck.unit_name, truck.start_loader), mv)
        tt = self._travel_s(truck, loader, dump, loaded=True)
        if tt is None:
            raise RuntimeError(f"no loaded route {loader}->{dump} for truck {tid}")
        if self.plan is not None:
            # couple sim tonnes to depletion EXACTLY: the cyclelog records what the model YIELDED
            # (the final load of a block is PARTIAL when remaining < sampled payload)
            try:
                res = self.plan.on_load(loader, payload)
                payload = res.taken_t
            except (KeyError, RuntimeError):
                # the face died between grant and load-complete: leave empty, redispatch
                self.loader_q[loader].release()
                self._dispatch_next(tid, loader)
                return
        self.loader_q[loader].release()
        self._emit(self.engine.now, tid, loader, "haul", payload)
        self.engine.after(tt, self._arrive_dump, tid, dump, payload)

    def _arrive_dump(self, tid: int, dump: int, payload: float) -> None:
        self.truck_pos[tid] = dump
        q = self.dump_q[dump]
        arrive_t = self.engine.now

        def granted() -> None:
            self.result.truck_wait_s += self.engine.now - arrive_t
            dt = self.rng.stream("dumptime")
            dump_s = float(NOMINAL_DUMP_MEAN_S * dt.lognormal(mean=-0.5 * DUMP_CV * DUMP_CV, sigma=DUMP_CV))
            self._emit(self.engine.now, tid, dump, "dump", payload)
            self.engine.after(dump_s, self._dumped, tid, dump, payload)

        q.request(granted)

    def _dumped(self, tid: int, dump: int, payload: float) -> None:
        self.dump_q[dump].release()
        self.result.tonnes += payload
        self.result.cycles += 1
        self._emit(self.engine.now, tid, dump, "return", 0.0)
        self._dispatch_next(tid, dump)

    def _dispatch_next(self, tid: int, at_node: int) -> None:
        truck = self.trucks[tid]
        mv = self._mine_view(truck, at_node)
        try:
            loader = self.policy.next_loader(TruckView(tid, truck.unit_name, truck.start_loader), mv)
        except RuntimeError:
            return                                       # plan exhausted: park the truck
        tt = mv.eta_s.get((tid, loader))
        if tt is None or tt == float("inf"):
            return                                       # unreachable (severed): park
        self.inbound[loader] += 1
        self.engine.after(tt, self._go_load, tid, loader)

    def run(self) -> ShiftResult:
        self.start()
        self.engine.run(self.until_s)
        self.result.events.sort(key=lambda e: (e["t"], e["truck_id"]))
        self.result.events_executed = self.engine.events_executed
        for nid, q in self.loader_q.items():
            self.result.loader_wait_s[nid] = q.total_wait_s
        return self.result


def run_shift(net: RoadNetwork, loaders: list[LoaderSpec], dumps: list[int],
              trucks: list[TruckSpec], policy: DispatchPolicy, seed: int,
              plan_context=None, until_s: float = 8 * 3600.0) -> ShiftResult:
    """Simulate one shift; returns the cyclelog events + KPIs. Deterministic in (inputs, seed)."""
    return _Sim(net, loaders, dumps, trucks, policy, seed, plan_context, until_s).run()
