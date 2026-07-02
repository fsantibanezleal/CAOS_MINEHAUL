"""Deterministic DES resources: FIFO queues, capacity slots, and DirectionZones with arbitration.

Every grant is FIFO by request order (the engine's monotone event sequencing feeds arrival order),
so runs are reproducible to the byte. `DirectionZoneResource` implements the three arbitration
policies of the constraints layer and carries the DEADLOCK DETECTOR: if any requester has waited
longer than `deadlock_timeout_s` while the zone made zero transitions, the run raises
`SimulationDeadlock` — a stuck model fails loudly instead of hanging.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from ..network.constraints import DirectionZone, ZonePolicy
from .engine import Engine, SimulationDeadlock

GrantCb = Callable[[], None]


@dataclass
class QueueResource:
    """capacity servers + FIFO queue (loaders, dump spots, junctions)."""
    engine: Engine
    capacity: int = 1
    in_service: int = 0
    _queue: deque[tuple[int, GrantCb]] = field(default_factory=deque)
    _seq: int = 0
    total_wait_s: float = 0.0
    served: int = 0
    _enq_t: dict[int, float] = field(default_factory=dict)

    def request(self, grant: GrantCb) -> None:
        self._seq += 1
        if self.in_service < self.capacity and not self._queue:
            self.in_service += 1
            self.served += 1
            grant()
        else:
            self._enq_t[self._seq] = self.engine.now
            self._queue.append((self._seq, grant))

    def release(self) -> None:
        if self.in_service <= 0:
            raise RuntimeError("release without service")
        self.in_service -= 1
        if self._queue:
            seq, grant = self._queue.popleft()
            self.total_wait_s += self.engine.now - self._enq_t.pop(seq)
            self.in_service += 1
            self.served += 1
            grant()

    @property
    def queue_len(self) -> int:
        return len(self._queue)


@dataclass
class SlotResource:
    """Counted slots without queuing semantics of their own (segment headway capacity)."""
    capacity: int
    used: int = 0

    def try_acquire(self) -> bool:
        if self.used < self.capacity:
            self.used += 1
            return True
        return False

    def release(self) -> None:
        if self.used <= 0:
            raise RuntimeError("slot release without acquire")
        self.used -= 1


@dataclass
class _ZoneWaiter:
    seq: int
    direction: int
    loaded: bool
    grant: GrantCb
    enq_t: float


@dataclass
class DirectionZoneResource:
    """Runtime arbitration of a single-lane bidirectional zone (spec: constraints.DirectionZone).

    States: idle (no direction), or active(direction) with `inside` vehicles <= max_in_zone.
    A vehicle may enter iff the zone is idle, or active in ITS direction with capacity left AND the
    policy does not hold it back. Opposing vehicles queue at the boundary. Direction release
    happens when the last inside vehicle exits; the policy then picks the next direction group.
    """
    engine: Engine
    spec: DirectionZone
    deadlock_timeout_s: float = 1800.0
    direction: int = 0                    # 0 idle, +1 / -1 active
    inside: int = 0
    passed_this_hold: int = 0
    hold_started_t: float = 0.0
    transitions: int = 0
    _seq: int = 0
    _waiting: deque[_ZoneWaiter] = field(default_factory=deque)
    total_wait_s: float = 0.0
    direction_flips: int = 0

    # ---- entry / exit ----
    def request_entry(self, direction: int, loaded: bool, grant: GrantCb) -> None:
        if direction not in (+1, -1):
            raise ValueError("direction must be +1 or -1")
        self._seq += 1
        w = _ZoneWaiter(self._seq, direction, loaded, grant, self.engine.now)
        if self._can_enter_now(w):
            self._admit(w)
        else:
            self._waiting.append(w)
            self._check_deadlock()

    def exit(self) -> None:
        if self.inside <= 0:
            raise RuntimeError("zone exit without entry")
        self.inside -= 1
        self.transitions += 1
        self._drain()

    # ---- internals ----
    def _can_enter_now(self, w: _ZoneWaiter) -> bool:
        if self.direction == 0:
            return True
        if self.direction != w.direction:
            return False
        if self.inside >= self.spec.max_in_zone:
            return False
        # group_batching: after k vehicles or max hold, the active direction yields if opposition waits
        if self.spec.policy is ZonePolicy.GROUP_BATCHING and self._opposition_waiting():
            if (self.passed_this_hold >= self.spec.batch_k or
                    self.engine.now - self.hold_started_t >= self.spec.max_hold_s):
                return False
        return True

    def _opposition_waiting(self) -> bool:
        return any(w.direction != self.direction for w in self._waiting)

    def _admit(self, w: _ZoneWaiter) -> None:
        if self.direction == 0:
            self.direction = w.direction
            self.hold_started_t = self.engine.now
            self.passed_this_hold = 0
            self.direction_flips += 1
        self.inside += 1
        self.passed_this_hold += 1
        self.transitions += 1
        self.total_wait_s += self.engine.now - w.enq_t
        w.grant()

    def _next_direction(self) -> int | None:
        """Policy choice of the next direction group once the zone empties (FIFO within groups)."""
        if not self._waiting:
            return None
        if self.spec.policy is ZonePolicy.LOADED_PRIORITY:
            # direction of the earliest-waiting LOADED vehicle, else earliest overall
            for w in self._waiting:
                if w.loaded:
                    return w.direction
        return self._waiting[0].direction   # lockout + group_batching: FIFO between groups

    def _drain(self) -> None:
        if self.inside > 0:
            # same-direction followers may still enter behind those inside
            self._admit_matching(self.direction)
            return
        self.direction = 0
        nxt = self._next_direction()
        if nxt is None:
            return
        self._admit_matching(nxt)

    def _admit_matching(self, direction: int) -> None:
        admitted = True
        while admitted:
            admitted = False
            for i, w in enumerate(self._waiting):
                if w.direction == direction:
                    probe = _ZoneWaiter(w.seq, w.direction, w.loaded, w.grant, w.enq_t)
                    if self._can_enter_now(probe):
                        del self._waiting[i]
                        self._admit(w)
                        admitted = True
                    break   # FIFO: only the FIRST matching waiter may be considered each pass

    def _check_deadlock(self) -> None:
        head = self._waiting[0] if self._waiting else None
        if head is None:
            return
        snapshot = self.transitions

        def probe() -> None:
            still = any(w.seq == head.seq for w in self._waiting)
            if still and self.transitions == snapshot:
                raise SimulationDeadlock(
                    f"zone {self.spec.id}: waiter blocked {self.deadlock_timeout_s:.0f}s with zero transitions")

        self.engine.after(self.deadlock_timeout_s, probe)
