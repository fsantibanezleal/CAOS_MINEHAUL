"""cyclelog/v1 writer + validator — the EXACT DispatchLab ingestion contract.

CSV: header exactly `t,truck_id,shovel_id,event,payload_t`, UTF-8, LF endings.
    t          float seconds, normalized so the first row is 0.0, 1 decimal
    truck_id   int (roster order stable per spec)
    shovel_id  loader id for load/haul; dump-site id for dump/return
    event      load -> haul -> dump -> return (per-truck legal order; rows time-sorted globally)
    payload_t  0 for load/return; the loaded tonnes (1 decimal, <= 400) for haul/dump

Event anchoring (the integration checkpoint vs DispatchLab's EmpiricalBlock — recorded here and in
docs/data-contract): loadMeanSec = t_haul - t_load; fullTravelMedian = t_dump - t_haul;
dumpMean = t_return - t_dump; emptyTravelMedian(+queue) = t_nextload - t_return. run_shift emits
exactly these semantics (load = loading service START, haul = departure loaded, dump = dumping
service START, return = departure empty).

`validate_cyclelog` is a faithful Python port of DispatchLab's ingestCycleLog checks — used in
tests AND the CLI, so every shipped artifact is gated by the SAME rules the consumer applies.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

EVENTS = ("load", "haul", "dump", "return")
NEXT = {"load": "haul", "haul": "dump", "dump": "return", "return": "load"}
PAYLOAD_MAX_T = 400.0
MF_RANGE = (0.4, 2.5)


def write_cyclelog(events: list[dict], path: str | Path) -> int:
    """Write run_shift events as cyclelog/v1. Returns the row count. Times re-zeroed + rounded 0.1 s
    AT THIS BOUNDARY ONLY (the engine clock is never rounded)."""
    if not events:
        raise ValueError("no events to write")
    rows = sorted(events, key=lambda e: (e["t"], e["truck_id"]))
    t0 = rows[0]["t"]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["t", "truck_id", "shovel_id", "event", "payload_t"])
        for e in rows:
            payload = e["payload_t"] if e["event"] in ("haul", "dump") else 0.0
            w.writerow([f"{e['t'] - t0:.1f}", int(e["truck_id"]), int(e["shovel_id"]),
                        e["event"], f"{min(payload, PAYLOAD_MAX_T):.1f}"])
    return len(rows)


@dataclass
class IngestReport:
    ok: bool
    rejected: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    n_rows: int = 0
    trucks: list[int] = field(default_factory=list)
    shovels: list[int] = field(default_factory=list)
    dumps: list[int] = field(default_factory=list)


def validate_cyclelog(path: str | Path) -> IngestReport:  # noqa: PLR0912 - the contract's rule list
    """The DispatchLab ingestCycleLog checks, ported faithfully (see module docstring)."""
    rep = IngestReport(ok=False)
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != ["t", "truck_id", "shovel_id", "event", "payload_t"]:
                rep.rejected.append({"row": -1, "reason": f"bad header {reader.fieldnames}"})
                return rep
            raw = list(reader)
    except OSError as e:
        rep.rejected.append({"row": -1, "reason": str(e)})
        return rep

    rows = []
    for i, r in enumerate(raw):
        try:
            t = float(r["t"]); truck = int(r["truck_id"]); node = int(r["shovel_id"])
            payload = float(r["payload_t"])
        except (TypeError, ValueError):
            rep.rejected.append({"row": i, "reason": "non-numeric field"})
            continue
        ev = r["event"]
        if ev not in EVENTS:
            rep.rejected.append({"row": i, "reason": f"unknown event {ev!r}"})
            continue
        if not (0.0 <= payload <= PAYLOAD_MAX_T):
            rep.rejected.append({"row": i, "reason": f"payload {payload} out of [0,{PAYLOAD_MAX_T}]"})
            continue
        rows.append({"t": t, "truck": truck, "node": node, "event": ev, "payload": payload})

    by_truck: dict[int, list[dict]] = {}
    for r in rows:
        by_truck.setdefault(r["truck"], []).append(r)
    for truck, lst in by_truck.items():
        lst.sort(key=lambda r: r["t"])
        state = "return"
        for r in lst:
            if r["t"] < lst[0]["t"]:
                rep.rejected.append({"row": -1, "reason": f"truck {truck}: non-monotonic t"})
                return rep
            if NEXT[state] != r["event"]:
                rep.rejected.append({"row": -1, "reason": f"truck {truck}: illegal {state}->{r['event']}"})
                return rep
            state = r["event"]
    if len(rows) < 8 or not by_truck:
        rep.rejected.append({"row": -1, "reason": "too few valid rows for a shift"})
        return rep

    shovels = sorted({r["node"] for r in rows if r["event"] == "load"})
    dumps = sorted({r["node"] for r in rows if r["event"] == "dump"})
    if not shovels or not dumps:
        rep.rejected.append({"row": -1, "reason": "no load or no dump events"})
        return rep
    if set(shovels) & set(dumps):
        rep.flags.append("a node appears as both shovel and dump")

    # empirical MF flag (loadMean over truck cycle), same estimate DispatchLab makes
    load_means = []
    cycles = []
    for lst in by_truck.values():
        loads = [r["t"] for r in lst if r["event"] == "load"]
        hauls = [r["t"] for r in lst if r["event"] == "haul"]
        load_means += [h - lo for lo, h in zip(loads, hauls)]
        cycles += [b - a for a, b in zip(loads, loads[1:])]
    if load_means and cycles:
        ml = sorted(load_means)[len(load_means) // 2]
        mc = sorted(cycles)[len(cycles) // 2]
        if mc > 0:
            mf = (len(by_truck) * ml) / (len(shovels) * mc)
            if not (MF_RANGE[0] <= mf <= MF_RANGE[1]):
                rep.flags.append(f"empirical MF {mf:.2f} outside [{MF_RANGE[0]},{MF_RANGE[1]}]")

    rep.ok = True
    rep.n_rows = len(rows)
    rep.trucks = sorted(by_truck)
    rep.shovels = shovels
    rep.dumps = dumps
    return rep
