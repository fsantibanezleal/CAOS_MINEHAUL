"""DES engine + resources (U5 acceptance): ordering, determinism, FIFO, zone arbitration, deadlock."""
import pytest

from minehaulsim.des.engine import Engine, SimulationDeadlock
from minehaulsim.des.resources import DirectionZoneResource, QueueResource, SlotResource
from minehaulsim.network.constraints import DirectionZone, ZonePolicy


def test_engine_executes_in_time_then_sequence_order():
    e = Engine()
    log: list[str] = []
    e.schedule(5.0, lambda: log.append("b"))
    e.schedule(1.0, lambda: log.append("a"))
    e.schedule(5.0, lambda: log.append("c"))     # same t as "b": schedule order wins
    e.run(until_s=10.0)
    assert log == ["a", "b", "c"]
    assert e.now == 10.0 and e.events_executed == 3


def test_engine_cancel_and_past_rejection():
    e = Engine()
    log: list[str] = []
    h = e.schedule(2.0, lambda: log.append("x"))
    e.cancel(h)
    e.run(5.0)
    assert log == []
    with pytest.raises(ValueError):
        e.schedule(1.0, lambda: None)            # now=5, the past is rejected


def test_engine_rerun_identical_sequence():
    def build_and_run() -> list[float]:
        e = Engine()
        out: list[float] = []
        def tick(k: int) -> None:
            out.append(e.now)
            if k < 5:
                e.after(3.7, tick, k + 1)
        e.schedule(0.0, tick, 0)
        e.run(100.0)
        return out
    assert build_and_run() == build_and_run()


def test_queue_resource_fifo_and_wait_accounting():
    e = Engine()
    q = QueueResource(engine=e, capacity=1)
    order: list[str] = []
    def user(name: str, service_s: float):
        def granted() -> None:
            order.append(name)
            e.after(service_s, q.release)
        return granted
    e.schedule(0.0, lambda: q.request(user("A", 10.0)))
    e.schedule(1.0, lambda: q.request(user("B", 10.0)))
    e.schedule(2.0, lambda: q.request(user("C", 10.0)))
    e.run(100.0)
    assert order == ["A", "B", "C"]
    # B waited 10-1=9, C waited 20-2=18
    assert q.total_wait_s == pytest.approx(27.0)
    assert q.served == 3 and q.queue_len == 0


def test_slot_resource_bounds():
    s = SlotResource(capacity=2)
    assert s.try_acquire() and s.try_acquire()
    assert not s.try_acquire()
    s.release()
    assert s.try_acquire()
    with pytest.raises(RuntimeError):
        SlotResource(capacity=1).release()


def _zone(policy: ZonePolicy, max_in_zone: int = 2, batch_k: int = 2) -> tuple[Engine, DirectionZoneResource]:
    e = Engine()
    spec = DirectionZone(id=1, segment_ids=(10,), policy=policy, max_in_zone=max_in_zone,
                         batch_k=batch_k, max_hold_s=1e9)
    return e, DirectionZoneResource(engine=e, spec=spec, deadlock_timeout_s=1800.0)


def test_zone_lockout_excludes_opposing_until_empty():
    e, z = _zone(ZonePolicy.LOCKOUT)
    log: list[str] = []
    # two +1 travelers enter at t=0 (zone empty), each 30 s inside; a -1 arrives at t=5 and must wait
    def enter(name: str, direction: int, dur: float):
        def granted() -> None:
            log.append(f"in:{name}@{e.now:.0f}")
            e.after(dur, z.exit)
        return granted
    e.schedule(0.0, lambda: z.request_entry(+1, True, enter("u1", +1, 30.0)))
    e.schedule(0.0, lambda: z.request_entry(+1, True, enter("u2", +1, 30.0)))
    e.schedule(5.0, lambda: z.request_entry(-1, False, enter("d1", -1, 20.0)))
    e.run(200.0)
    assert log == ["in:u1@0", "in:u2@0", "in:d1@30"]   # d1 only after the zone drains
    assert z.direction_flips == 2


def test_zone_capacity_serializes_same_direction():
    e, z = _zone(ZonePolicy.LOCKOUT, max_in_zone=1)
    times: list[float] = []
    def enter(dur: float):
        def granted() -> None:
            times.append(e.now)
            e.after(dur, z.exit)
        return granted
    for k in range(3):
        e.schedule(0.0 + k, lambda d=10.0: z.request_entry(+1, True, enter(d)))
    e.run(100.0)
    assert times == [0.0, 10.0, 20.0]                   # one at a time


def test_zone_loaded_priority_picks_loaded_group_next():
    e, z = _zone(ZonePolicy.LOADED_PRIORITY)
    log: list[str] = []
    def enter(name: str, dur: float):
        def granted() -> None:
            log.append(name)
            e.after(dur, z.exit)
        return granted
    # active +1 empty-truck group; then an EMPTY -1 waiter arrives BEFORE a LOADED +1... make groups:
    e.schedule(0.0, lambda: z.request_entry(+1, False, enter("e_up", 30.0)))
    e.schedule(1.0, lambda: z.request_entry(-1, False, enter("e_down", 30.0)))     # earliest waiter
    e.schedule(2.0, lambda: z.request_entry(-1, True, enter("LOADED_down", 30.0)))
    e.run(300.0)
    # on drain, loaded_priority chooses the direction of the earliest LOADED waiter (-1 here),
    # and FIFO admits e_down first within that group
    assert log == ["e_up", "e_down", "LOADED_down"]
    assert z.total_wait_s > 0


def test_zone_group_batching_yields_after_k():
    e, z = _zone(ZonePolicy.GROUP_BATCHING, max_in_zone=4, batch_k=2)
    log: list[str] = []
    def enter(name: str, dur: float):
        def granted() -> None:
            log.append(f"{name}@{e.now:.0f}")
            e.after(dur, z.exit)
        return granted
    # +1 vehicles arrive at 0,1,2 (10 s inside each); a -1 waiter arrives at 0.5
    e.schedule(0.0, lambda: z.request_entry(+1, True, enter("u1", 10.0)))
    e.schedule(1.0, lambda: z.request_entry(+1, True, enter("u2", 10.0)))
    e.schedule(0.5, lambda: z.request_entry(-1, True, enter("d1", 10.0)))
    e.schedule(2.0, lambda: z.request_entry(+1, True, enter("u3", 10.0)))
    e.run(300.0)
    # batch_k=2: u1,u2 pass; u3 must NOT enter while d1 waits; d1 goes when the zone drains; u3 last
    assert log == ["u1@0", "u2@1", "d1@11", "u3@21"]


def test_zone_deadlock_detector_fires_loudly():
    e, z = _zone(ZonePolicy.LOCKOUT, max_in_zone=1)
    # a vehicle enters and NEVER exits; an opposing waiter then starves -> SimulationDeadlock
    e.schedule(0.0, lambda: z.request_entry(+1, True, lambda: None))
    e.schedule(1.0, lambda: z.request_entry(-1, False, lambda: None))
    with pytest.raises(SimulationDeadlock):
        e.run(10_000.0)
