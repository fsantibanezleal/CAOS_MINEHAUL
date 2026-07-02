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

from collections import deque
from dataclasses import dataclass, field

from ..equipment.catalog import LHDS, LOADERS, TRUCKS, LoaderClass, TruckClass
from ..network.graph import RoadNetwork
from ..network.routing import Router
from ..rng import RngManager
from .dispatch import DispatchPolicy, LoaderView, MineView, TruckView
from .engine import Engine
from .failures import FailureConfig, FailureState
from .materials import OrePassRuntime, ShaftBinRuntime
from .resources import QueueResource
from .traversal import TrafficState

NOMINAL_DUMP_MEAN_S = 55.0
DUMP_CV = 0.15
CLOSURE_RETRY_S = 300.0     # parked-by-closure trucks re-ask for a route this often


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


@dataclass(frozen=True)
class LhdSpec:
    """An underground LHD: digs its drawpoints round-robin, trams to its ore-pass tip, tips,
    returns. Couples to the truck fleet ONLY through the pass inventory (U10)."""
    lhd_id: int
    unit_name: str                       # LHDS catalog key (LHD_14 / LHD_18)
    drawpoints: tuple[int, ...]
    tip_node: int
    pass_id: int


@dataclass(frozen=True)
class OrePassSpec:
    """Runtime ore-pass document: the chute node (a cyclelog shovel) + finite capacity."""
    pass_id: int
    chute_node: int
    capacity_t: float


@dataclass(frozen=True)
class ShaftBinSpec:
    node: int
    capacity_t: float
    hoist_tph: float


@dataclass
class ShiftResult:
    events: list[dict] = field(default_factory=list)     # cyclelog/v1 rows (t seconds, floats)
    tonnes: float = 0.0
    cycles: int = 0
    truck_wait_s: float = 0.0
    loader_wait_s: dict[int, float] = field(default_factory=dict)
    events_executed: int = 0
    materials: dict = field(default_factory=dict)        # per-pass conservation + bin summary
    downtime: dict = field(default_factory=dict)         # failures: per-unit repair seconds


class _Sim:
    def __init__(self, net: RoadNetwork, loaders: list[LoaderSpec], dumps: list[int],
                 trucks: list[TruckSpec], policy: DispatchPolicy, seed: int,
                 plan_context=None, until_s: float = 8 * 3600.0,
                 zones=None, junctions=None, fast_mode: bool = False,
                 lhds: list[LhdSpec] | None = None,
                 ore_passes: list[OrePassSpec] | None = None,
                 shaft_bin: ShaftBinSpec | None = None,
                 failures: FailureConfig | None = None) -> None:
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
        self._zones_spec = zones or {}
        self._junctions_spec = junctions or {}
        self.fast_mode = fast_mode
        self.traffic: TrafficState | None = None
        self._rebuild_router()
        # resources
        self.loader_q: dict[int, QueueResource] = {
            ls.node_id: QueueResource(engine=self.engine, capacity=ls.n_spots) for ls in loaders}
        self.dump_q: dict[int, QueueResource] = {
            d: QueueResource(engine=self.engine, capacity=1) for d in dumps}
        self.loader_busy_until: dict[int, float] = {ls.node_id: 0.0 for ls in loaders}
        self.inbound: dict[int, int] = {ls.node_id: 0 for ls in loaders}
        self.truck_pos: dict[int, int] = {}              # truck -> current node
        # ---- underground material coupling (U10; all empty/None for open pits)
        self.lhds = {sp.lhd_id: sp for sp in (lhds or [])}
        self.passes: dict[int, OrePassRuntime] = {
            op.pass_id: OrePassRuntime(op.pass_id, op.chute_node, op.capacity_t)
            for op in (ore_passes or [])}
        self._pass_by_chute: dict[int, OrePassRuntime] = {
            p.chute_node: p for p in self.passes.values()}
        # trucks parked UNDER a chute (holding its loading spot) until inventory covers them
        self._chute_wait: dict[int, deque] = {p.chute_node: deque() for p in self.passes.values()}
        # LHDs parked at a FULL pass tip, in arrival order
        self._tip_wait: dict[int, deque] = {pid: deque() for pid in self.passes}
        self._chute_payload: dict[int, float] = {}       # truck -> reserved chute payload
        self._lhd_next_dp: dict[int, int] = {sp.lhd_id: 0 for sp in (lhds or [])}
        self.bin = (ShaftBinRuntime(shaft_bin.node, shaft_bin.capacity_t, shaft_bin.hoist_tph)
                    if shaft_bin else None)
        # ---- failure processes (U11; None = perfect equipment, the default)
        self.failures: FailureState | None = None
        if failures is not None:
            self.failures = FailureState(config=failures,
                                         rng_truck=self.rng.stream("fail.truck"),
                                         rng_loader=self.rng.stream("fail.loader"))
            for t in trucks:
                self.failures.init_truck(t.truck_id)
            for ls in loaders:
                self.failures.init_loader(ls.node_id)

    # ---- plan-aware network/router ----
    def _rebuild_router(self) -> None:
        if self.plan is not None:
            self._net = self.plan.effective_network(self.base_net)
            self._revision = self.plan.overlay_revision()
        else:
            self._net = self.base_net
            self._revision = 0
        self.router = Router(self._net, junctions=self._junctions_spec)
        if not self.fast_mode:
            if self.traffic is None:
                self.traffic = TrafficState(self.engine, self._net, self._zones_spec, self._junctions_spec)
            else:
                self.traffic.rebind(self._net)

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
        r = self._route(truck, a, b, loaded)
        return None if r is None else r.time_s

    def _route(self, truck: TruckSpec, a: int, b: int, loaded: bool):
        closed, caps = self._routing_state()
        unit = TRUCKS[truck.unit_name]
        return self.router.route(a, b, unit, loaded=loaded, closed=self._closed_now(closed),
                                 speed_caps=caps)

    def _closed_now(self, closed: frozenset[int]) -> frozenset[int]:
        """Overlay closures + any active maintenance-window closures (failures config)."""
        if self.failures is None or not self.failures.config.closures:
            return closed
        return closed | self.failures.closed_segments(self.engine.now)

    def _go(self, truck: TruckSpec, a: int, b: int, loaded: bool, payload: float,
            arrive_cb, *cb_args) -> bool:
        """Send a truck a->b: traffic traversal when enabled, else the free-flow single event."""
        unit = TRUCKS[truck.unit_name]
        return self._go_unit(unit, a, b, loaded, unit.empty_t + (payload if loaded else 0.0),
                             arrive_cb, *cb_args)

    def _go_unit(self, unit, a: int, b: int, loaded: bool, gvw: float,
                 arrive_cb, *cb_args) -> bool:
        """Unit-generic travel (trucks AND LHDs share the network + traffic rules)."""
        closed, caps = self._routing_state()
        closed = self._closed_now(closed)
        r = self.router.route(a, b, unit, loaded=loaded, closed=closed, speed_caps=caps)
        if r is None:
            return False
        if self.traffic is None:
            self.engine.after(r.time_s, arrive_cb, *cb_args)
            return True
        self.traffic.traverse(unit, gvw, loaded, r, caps, lambda: arrive_cb(*cb_args))
        return True

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

    # ---- the LHD loop (U10): dig -> tram to tip -> tip (or wait at a full pass) -> return ----
    def _lhd_start(self, lid: int) -> None:
        sp = self.lhds[lid]
        self._lhd_dig(lid, sp.drawpoints[0])

    def _lhd_dig(self, lid: int, dp: int) -> None:
        sp = self.lhds[lid]
        unit = LHDS[sp.unit_name]
        rng = self.rng.stream("lhd.dig")
        dig_s = float(unit.dig_time_s * rng.lognormal(
            mean=-0.5 * unit.dig_time_cv ** 2, sigma=unit.dig_time_cv))
        self.engine.after(dig_s, self._lhd_to_tip, lid, dp)

    def _lhd_to_tip(self, lid: int, dp: int) -> None:
        sp = self.lhds[lid]
        unit = LHDS[sp.unit_name]
        bkt = self.rng.stream("lhd.bucket")
        bucket = float(max(0.5 * unit.bucket_t,
                           min(unit.bucket_t + 3 * unit.bucket_sd_t,
                               bkt.normal(unit.bucket_t, unit.bucket_sd_t))))
        if not self._go_unit(unit, dp, sp.tip_node, True, unit.empty_t + bucket,
                             self._lhd_at_tip, lid, dp, bucket):
            raise RuntimeError(f"LHD {lid}: no route {dp}->{sp.tip_node}")

    def _lhd_at_tip(self, lid: int, dp: int, bucket: float) -> None:
        sp = self.lhds[lid]
        p = self.passes[sp.pass_id]
        if not p.can_tip(bucket):
            self._tip_wait[sp.pass_id].append((lid, dp, bucket))   # full pass parks the LHD
            return
        p.tip(bucket)
        self._settle(sp.pass_id)
        self._lhd_after_tip(lid)

    def _lhd_after_tip(self, lid: int) -> None:
        """Tip committed: return empty and dig the next drawpoint round-robin."""
        sp = self.lhds[lid]
        self._lhd_next_dp[lid] = (self._lhd_next_dp[lid] + 1) % len(sp.drawpoints)
        nxt = sp.drawpoints[self._lhd_next_dp[lid]]
        unit = LHDS[sp.unit_name]
        if not self._go_unit(unit, sp.tip_node, nxt, False, unit.empty_t,
                             self._lhd_dig, lid, nxt):
            raise RuntimeError(f"LHD {lid}: no route {sp.tip_node}->{nxt}")

    def _settle(self, pass_id: int) -> None:
        """Serve both wait queues of a pass, FIFO, iteratively (no recursion): trucks parked
        under the chute while inventory covers them; then LHDs parked at the full tip while
        their bucket fits; repeat until neither head can be served."""
        p = self.passes[pass_id]
        chute_w = self._chute_wait[p.chute_node]
        tip_w = self._tip_wait[pass_id]
        progress = True
        while progress:
            progress = False
            while chute_w and p.can_draw(chute_w[0][1]):
                tid, payload, resume = chute_w.popleft()
                p.draw(payload)
                resume()
                progress = True
            while tip_w and p.can_tip(tip_w[0][2]):
                lid, _dp, bucket = tip_w.popleft()
                p.tip(bucket)
                self._lhd_after_tip(lid)
                progress = True

    # ---- the cycle ----
    def start(self) -> None:
        stagger = self.rng.stream("init")
        for tid in sorted(self.trucks):
            t = self.trucks[tid]
            self.truck_pos[tid] = t.start_loader
            self.engine.schedule(float(stagger.uniform(0.0, 60.0)), self._go_load, tid, t.start_loader)
        for lid in sorted(self.lhds):
            self.engine.schedule(float(stagger.uniform(0.0, 60.0)), self._lhd_start, lid)

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

        def start_loading() -> None:
            # U11: loader downtime surfaces at service start — the granted truck WAITS at the
            # face through the repair (dispatch sees it via est_free_s in the MineView)
            if self.failures is not None and self.failures.loader_due(loader, self.engine.now):
                rep = self.failures.loader_repair_s(loader, self.engine.now)
                self.loader_busy_until[loader] = self.engine.now + rep
                self.engine.after(rep, start_loading)
                return
            truck = self.trucks[tid]
            ls = self.loader_specs[loader]
            mean = self._load_mean_s(ls, truck)
            cv = LOADERS[ls.loader_class].time_cv
            lt = self.rng.stream("loadtime")
            load_s = float(mean * lt.lognormal(mean=-0.5 * cv * cv, sigma=cv))
            self._emit(self.engine.now, tid, loader, "load", 0.0)
            self.loader_busy_until[loader] = self.engine.now + load_s
            self.engine.after(load_s, self._loaded, tid, loader)

        def granted() -> None:
            if self.plan is not None and not self.plan.is_diggable(loader):
                # face became non-diggable while queued: release the spot and redispatch empty
                q.release()
                self._dispatch_next(tid, loader)
                return
            self.result.truck_wait_s += self.engine.now - arrive_t
            p = self._pass_by_chute.get(loader)
            if p is None:
                start_loading()
                return
            # CHUTE (U10): the payload is drawn from the ore-pass inventory. Reserve it at grant;
            # an empty pass parks the truck UNDER the chute (holding the spot — the physical
            # reality) until LHD tips cover it (FIFO via _settle).
            unit: TruckClass = TRUCKS[self.trucks[tid].unit_name]
            pay_rng = self.rng.stream("payload")
            payload = float(max(0.5 * unit.payload_mean_t,
                                min(400.0, pay_rng.normal(unit.payload_mean_t, unit.payload_sd_t))))
            self._chute_payload[tid] = payload
            grant_t = self.engine.now

            def resume() -> None:
                self.result.truck_wait_s += self.engine.now - grant_t
                start_loading()

            if p.can_draw(payload):
                p.draw(payload)
                self._settle(p.pass_id)                  # freed capacity may unblock tipping LHDs
                start_loading()
            else:
                self._chute_wait[loader].append((tid, payload, resume))

        q.request(granted)

    def _loaded(self, tid: int, loader: int) -> None:
        truck = self.trucks[tid]
        unit: TruckClass = TRUCKS[truck.unit_name]
        reserved = self._chute_payload.pop(tid, None)
        if reserved is not None:
            payload = reserved                           # chute: drawn from the pass at grant
        else:
            pay_rng = self.rng.stream("payload")
            payload = float(max(0.5 * unit.payload_mean_t,
                                min(400.0, pay_rng.normal(unit.payload_mean_t, unit.payload_sd_t))))
        self._depart_loaded(tid, loader, payload)

    def _depart_loaded(self, tid: int, loader: int, payload: float) -> None:
        """Payload is FINAL here; retry-safe (a closure window may hold the loaded truck at the
        face — it keeps the loader spot, which is the physical reality of a blocked ramp)."""
        truck = self.trucks[tid]
        # QUOTE the outbound route BEFORE depleting: if this load completes the bench, the face
        # spur retires — but the truck physically leaves on the geometry it arrived on (design P4:
        # in-flight legs finish on the old geometry).
        mv = self._mine_view(truck, loader)
        dump = self.policy.next_dump(TruckView(tid, truck.unit_name, truck.start_loader), mv)
        if self._route(truck, loader, dump, loaded=True) is None:
            if self.failures is not None and self.failures.config.closures:
                self.engine.after(CLOSURE_RETRY_S, self._depart_loaded, tid, loader, payload)
                return
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
        self._go(truck, loader, dump, True, payload, self._arrive_dump, tid, dump, payload)

    def _arrive_dump(self, tid: int, dump: int, payload: float) -> None:
        self.truck_pos[tid] = dump
        q = self.dump_q[dump]
        arrive_t = self.engine.now

        def do_dump() -> None:
            dt = self.rng.stream("dumptime")
            dump_s = float(NOMINAL_DUMP_MEAN_S * dt.lognormal(mean=-0.5 * DUMP_CV * DUMP_CV, sigma=DUMP_CV))
            self._emit(self.engine.now, tid, dump, "dump", payload)
            self.engine.after(dump_s, self._dumped, tid, dump, payload)

        def granted() -> None:
            self.result.truck_wait_s += self.engine.now - arrive_t
            if self.bin is not None and dump == self.bin.node:
                # SHAFT BIN (U10): hoisting drains the bin continuously; a full bin makes the
                # truck hold the dump spot for the EXACT closed-form time until space exists.
                wait = self.bin.wait_for_space_s(self.engine.now, payload)

                def bin_dump() -> None:
                    self.result.truck_wait_s += wait
                    self.bin.dump(self.engine.now, payload)
                    do_dump()

                if wait > 0.0:
                    self.engine.after(wait, bin_dump)
                else:
                    bin_dump()
                return
            do_dump()

        q.request(granted)

    def _dumped(self, tid: int, dump: int, payload: float) -> None:
        self.dump_q[dump].release()
        self.result.tonnes += payload
        self.result.cycles += 1
        self._emit(self.engine.now, tid, dump, "return", 0.0)
        self._dispatch_next(tid, dump)

    def _dispatch_next(self, tid: int, at_node: int) -> None:
        # U11: breakdowns materialize at the cycle boundary — the truck finished its leg and
        # parks HERE for the repair (v1 semantics: no mid-segment blocking, documented)
        if self.failures is not None and self.failures.truck_due(tid, self.engine.now):
            rep = self.failures.truck_repair_s(tid, self.engine.now)
            self.engine.after(rep, self._dispatch_next, tid, at_node)
            return
        truck = self.trucks[tid]
        mv = self._mine_view(truck, at_node)
        try:
            loader = self.policy.next_loader(TruckView(tid, truck.unit_name, truck.start_loader), mv)
        except RuntimeError:
            return                                       # plan exhausted: park the truck
        tt = mv.eta_s.get((tid, loader))
        if tt is None or tt == float("inf"):
            # unreachable: a maintenance window may reopen the road — retry then. A severed
            # plan/damage closure has no window, so without closures the truck stays parked.
            if self.failures is not None and self.failures.config.closures:
                self.engine.after(CLOSURE_RETRY_S, self._dispatch_next, tid, at_node)
            return
        self.inbound[loader] += 1
        if not self._go(truck, at_node, loader, False, 0.0, self._go_load, tid, loader):
            self.inbound[loader] = max(0, self.inbound[loader] - 1)

    def run(self) -> ShiftResult:
        self.start()
        self.engine.run(self.until_s)
        self.result.events.sort(key=lambda e: (e["t"], e["truck_id"]))
        self.result.events_executed = self.engine.events_executed
        for nid, q in self.loader_q.items():
            self.result.loader_wait_s[nid] = q.total_wait_s
        for pid, p in sorted(self.passes.items()):
            self.result.materials[f"pass_{pid}"] = p.summary()
        if self.passes:
            # tonnes drawn from a pass by trucks still LOADING at cutoff (their 'haul' event
            # never fired) — the term that closes the conservation balance
            self.result.materials["chute_in_flight_t"] = round(
                sum(self._chute_payload.values()), 6)
        if self.bin is not None:
            self.result.materials["shaft_bin"] = self.bin.summary(self.engine.now)
        if self.failures is not None:
            self.result.downtime = {
                "truck_s": {k: round(v, 3) for k, v in
                            sorted(self.failures.truck_downtime_s.items()) if v > 0},
                "loader_s": {k: round(v, 3) for k, v in
                             sorted(self.failures.loader_downtime_s.items()) if v > 0},
            }
        return self.result


def run_shift(net: RoadNetwork, loaders: list[LoaderSpec], dumps: list[int],
              trucks: list[TruckSpec], policy: DispatchPolicy, seed: int,
              plan_context=None, until_s: float = 8 * 3600.0,
              zones=None, junctions=None, fast_mode: bool = False,
              lhds: list[LhdSpec] | None = None,
              ore_passes: list[OrePassSpec] | None = None,
              shaft_bin: ShaftBinSpec | None = None,
              failures: FailureConfig | None = None) -> ShiftResult:
    """Simulate one shift; returns the cyclelog events + KPIs. Deterministic in (inputs, seed).
    Traffic (per-segment slots + no-overtake headway + direction zones + junctions) is ON by
    default; fast_mode=True bypasses it for quick statistical runs (free-flow times).
    Underground (U10): pass `lhds` + `ore_passes` (and optionally `shaft_bin`) to couple an LHD
    fleet to the truck fleet through ore-pass inventories; open-pit runs leave them None.
    Failures (U11): pass a `FailureConfig` for breakdowns / loader downtime / closure windows;
    None (the default) = perfect equipment."""
    return _Sim(net, loaders, dumps, trucks, policy, seed, plan_context, until_s,
                zones, junctions, fast_mode, lhds, ore_passes, shaft_bin, failures).run()
