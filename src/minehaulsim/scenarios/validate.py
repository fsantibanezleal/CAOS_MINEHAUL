"""Named validity gates every generated MineSpec must pass (each check tested individually).

Order (cheap structural checks first, the smoke simulation last):
    contract_ready       roster fits cyclelog/v1: shovel ids 1..N, dump ids >= 101, payloads <= 400 t
    connectivity         every loader reaches every dump AND back, for EVERY truck class, on the
                         constrained graph (width / one-way / zones respected)
    grades               no segment exceeds its class grade limit (open pit 11%; decline 15.5%)
    geometry_sane        floor radius >= 40 m; every pit elevation within [-depth, 0]
    traffic_sane         every DirectionZone shorter than 450 m (a bay at least that often);
                         junction degree <= 5
    throughput_sane      static match factor in [0.5, 2.2]; estimated cycle in [6, 90] min
    deadlock_free_smoke  a smoke run with the default policy completes with no SimulationDeadlock,
                         >= 8 cyclelog rows, >= 1 load per loader and >= 1 completed cycle.
                         The horizon is max(30 sim-min, 2.5 x estimated cycle) — a fixed 30 min
                         would false-fail deep pits whose single cycle exceeds it.

`diversity_signature` is the batch-mode structural fingerprint (ramp style, depth bucket, roster
and network shape): generate_batch(ensure_diverse=True) rejects a spec whose signature repeats.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..des.engine import SimulationDeadlock
from ..equipment.catalog import LOADERS, TRUCKS
from ..network.routing import Router
from .spec import MineSpec, RuntimeBundle

MAX_GRADE_PCT = {"openpit": 11.0, "underground": 15.5}
ZONE_MAX_LEN_M = 450.0
JUNCTION_MAX_DEGREE = 5
MF_BOUNDS = (0.5, 2.2)
CYCLE_BOUNDS_MIN = (6.0, 90.0)
SMOKE_MIN_S = 1800.0
NOMINAL_DUMP_S = 55.0


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ValidationReport:
    checks: list[CheckResult]

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def failing(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]

    def detail(self, name: str) -> str:
        for c in self.checks:
            if c.name == name:
                return c.detail
        raise KeyError(name)


# ---------------------------------------------------------------- shared estimators

def load_time_s(loader_class: str, unit_name: str) -> float:
    """Mean time to load one truck: full passes + spotting (same formula the sim uses)."""
    import math
    lc = LOADERS[loader_class]
    payload = TRUCKS[unit_name].payload_mean_t
    return math.ceil(payload / lc.pass_t) * lc.pass_time_s + lc.spot_time_s


def representative_cycle_s(rt: RuntimeBundle) -> float | None:
    """Free-flow cycle estimate for the DEEPEST loader with the LARGEST truck class in the roster:
    loaded haul to the primary dump + empty return + load + dump. None if unroutable."""
    if not rt.loaders or not rt.dumps or not rt.trucks:
        return None
    router = Router(rt.net, junctions=rt.junctions)
    face = min((ls.node_id for ls in rt.loaders),
               key=lambda n: (rt.net.nodes[n].pos[2], n))          # deepest, ties by id
    unit_name = max((t.unit_name for t in rt.trucks),
                    key=lambda u: TRUCKS[u].payload_mean_t)
    unit = TRUCKS[unit_name]
    dump = rt.dumps[0]
    try:
        loaded = router.route(face, dump, unit, loaded=True)
        empty = router.route(dump, face, unit, loaded=False)
    except ValueError:                                   # unknown node: malformed spec
        return None
    if loaded is None or empty is None:
        return None
    ls = next(x for x in rt.loaders if x.node_id == face)
    return (loaded.time_s + empty.time_s + load_time_s(ls.loader_class, unit_name)
            + NOMINAL_DUMP_S)


def static_match_factor(rt: RuntimeBundle, cycle_s: float) -> float:
    """MF = (trucks x time-to-load-one) / (loaders x truck cycle)."""
    unit_name = max((t.unit_name for t in rt.trucks), key=lambda u: TRUCKS[u].payload_mean_t)
    mean_load = sum(load_time_s(ls.loader_class, unit_name) for ls in rt.loaders) / len(rt.loaders)
    return (len(rt.trucks) * mean_load) / (len(rt.loaders) * cycle_s)


# ---------------------------------------------------------------- the checks

def check_contract_ready(spec: MineSpec, rt: RuntimeBundle) -> CheckResult:
    shovel_ids = sorted(ls.node_id for ls in rt.loaders)
    if shovel_ids != list(range(1, len(shovel_ids) + 1)):
        return CheckResult("contract_ready", False, f"shovel ids not 1..N: {shovel_ids}")
    if any(d < 101 for d in rt.dumps):
        return CheckResult("contract_ready", False, f"dump id < 101 in {rt.dumps}")
    truck_ids = sorted(t.truck_id for t in rt.trucks)
    if truck_ids != list(range(1, len(truck_ids) + 1)):
        return CheckResult("contract_ready", False, f"truck ids not 1..N: {truck_ids}")
    for t in rt.trucks:
        u = TRUCKS[t.unit_name]
        if u.payload_mean_t + 3 * u.payload_sd_t > 400.0:
            return CheckResult("contract_ready", False, f"{t.unit_name} can exceed 400 t")
    return CheckResult("contract_ready", True)


def check_connectivity(spec: MineSpec, rt: RuntimeBundle) -> CheckResult:
    router = Router(rt.net, junctions=rt.junctions)
    for unit_name in sorted({t.unit_name for t in rt.trucks}):
        unit = TRUCKS[unit_name]
        for ls in rt.loaders:
            for dump in rt.dumps:
                try:
                    loaded = router.route(ls.node_id, dump, unit, loaded=True)
                    empty = router.route(dump, ls.node_id, unit, loaded=False)
                except ValueError as e:                      # unknown node: malformed spec
                    return CheckResult("connectivity", False, str(e))
                if loaded is None:
                    return CheckResult("connectivity", False,
                                       f"{unit_name}: no loaded route {ls.node_id}->{dump}")
                if empty is None:
                    return CheckResult("connectivity", False,
                                       f"{unit_name}: no empty route {dump}->{ls.node_id}")
    return CheckResult("connectivity", True)


def check_grades(spec: MineSpec, rt: RuntimeBundle) -> CheckResult:
    limit = MAX_GRADE_PCT.get(spec.kind, 11.0)
    for s in rt.net.segments.values():
        if abs(s.grade_pct) > limit + 1e-9:
            return CheckResult("grades", False, f"segment {s.id} grade {s.grade_pct}% > {limit}%")
    return CheckResult("grades", True)


def check_geometry_sane(spec: MineSpec, rt: RuntimeBundle) -> CheckResult:
    floor_r = float(spec.params.get("floor_r_min_m", 0.0))
    if floor_r < 40.0:
        return CheckResult("geometry_sane", False, f"floor radius {floor_r:.1f} m < 40 m")
    depth = float(spec.params.get("depth_m", 0.0))
    for n in rt.net.nodes.values():
        if not (-depth - 1e-6 <= n.pos[2] <= 1e-6):
            return CheckResult("geometry_sane", False,
                               f"node {n.id} elevation {n.pos[2]:.1f} outside [-{depth:.0f}, 0]")
    return CheckResult("geometry_sane", True)


def check_traffic_sane(spec: MineSpec, rt: RuntimeBundle) -> CheckResult:
    for z in rt.zones.values():
        missing = [sid for sid in z.segment_ids if sid not in rt.net.segments]
        if missing:
            return CheckResult("traffic_sane", False,
                               f"zone {z.id} references missing segments {missing}")
        total = sum(rt.net.segments[sid].length_m for sid in z.segment_ids)
        if total >= ZONE_MAX_LEN_M:
            return CheckResult("traffic_sane", False,
                               f"zone {z.id} spans {total:.0f} m >= {ZONE_MAX_LEN_M} m")
    degree: dict[int, int] = {}
    for s in rt.net.segments.values():
        degree[s.a] = degree.get(s.a, 0) + 1
        degree[s.b] = degree.get(s.b, 0) + 1
    for jid in rt.junctions:
        if degree.get(jid, 0) > JUNCTION_MAX_DEGREE:
            return CheckResult("traffic_sane", False,
                               f"junction {jid} degree {degree[jid]} > {JUNCTION_MAX_DEGREE}")
    return CheckResult("traffic_sane", True)


def check_throughput_sane(spec: MineSpec, rt: RuntimeBundle) -> CheckResult:
    cycle_s = representative_cycle_s(rt)
    if cycle_s is None:
        return CheckResult("throughput_sane", False, "cycle estimate unroutable")
    cyc_min = cycle_s / 60.0
    if not (CYCLE_BOUNDS_MIN[0] <= cyc_min <= CYCLE_BOUNDS_MIN[1]):
        return CheckResult("throughput_sane", False,
                           f"estimated cycle {cyc_min:.1f} min outside {CYCLE_BOUNDS_MIN}")
    mf = static_match_factor(rt, cycle_s)
    if not (MF_BOUNDS[0] <= mf <= MF_BOUNDS[1]):
        return CheckResult("throughput_sane", False,
                           f"static match factor {mf:.2f} outside {MF_BOUNDS}")
    return CheckResult("throughput_sane", True)


def check_deadlock_free_smoke(spec: MineSpec, rt: RuntimeBundle) -> CheckResult:
    est_cycle = float(spec.est.get("cycle_s", 0.0))
    horizon = max(SMOKE_MIN_S, 2.5 * est_cycle)
    try:
        res = spec.run(seed=spec.seed + 1, until_s=horizon)
    except SimulationDeadlock as e:
        return CheckResult("deadlock_free_smoke", False, f"deadlock: {e}")
    if len(res.events) < 8:
        return CheckResult("deadlock_free_smoke", False,
                           f"only {len(res.events)} cyclelog rows in {horizon / 60:.0f} min")
    served = {e["shovel_id"] for e in res.events if e["event"] == "load"}
    missing = sorted(set(ls.node_id for ls in rt.loaders) - served)
    if missing:
        return CheckResult("deadlock_free_smoke", False, f"loaders never served: {missing}")
    if res.cycles < 1:
        return CheckResult("deadlock_free_smoke", False, "no completed cycle in the smoke run")
    return CheckResult("deadlock_free_smoke", True)


CHECKS = (check_contract_ready, check_connectivity, check_grades, check_geometry_sane,
          check_traffic_sane, check_throughput_sane, check_deadlock_free_smoke)


def validate_spec(spec: MineSpec, smoke: bool = True) -> ValidationReport:
    """Run all gates in order; stops at nothing (the report lists every failing check)."""
    rt = spec.to_runtime()
    checks = [c for c in CHECKS if smoke or c is not check_deadlock_free_smoke]
    return ValidationReport(checks=[c(spec, rt) for c in checks])


def diversity_signature(spec: MineSpec) -> tuple:
    """Structural fingerprint for batch diversity (module docstring)."""
    n_nodes = len(spec.network["nodes"])
    return (
        spec.kind,
        spec.params.get("ramp_style", spec.params.get("access_style", "?")),
        int(spec.params.get("n_benches", spec.params.get("n_levels", 0))) // 4,
        len(spec.loaders),
        len(spec.dumps),
        int(spec.params.get("n_surface_junctions", 0)),
        n_nodes // 20,
        spec.params.get("flow_mode", "truck_surface"),
    )
