"""The event-scheduling DES engine: an explicit heap loop, no process coroutines.

Why event-scheduling and not simpy-style processes: (1) SPEED — the perf target (>= 20k events/s)
rules out generator-switching overhead; (2) AUDITABILITY — one explicit loop, one clock, one
ordering rule; (3) DETERMINISM — heap entries are (t, seq, ...) with `seq` a monotone counter, so
same-time events pop in schedule order on every platform (no float-tie ambiguity ever reaches the
comparator).

The clock is float seconds. cyclelog/v1 rounds to 0.1 s AT EXPORT ONLY — never inside the engine.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Callable


class SimulationDeadlock(RuntimeError):
    """Raised loudly by resources when progress stalls (never hang a run silently)."""


@dataclass(frozen=True)
class EventHandle:
    t: float
    seq: int


@dataclass
class Engine:
    now: float = 0.0
    _heap: list[tuple[float, int, Callable[..., None], tuple[Any, ...]]] = field(default_factory=list)
    _seq: int = 0
    _cancelled: set[int] = field(default_factory=set)
    events_executed: int = 0

    def schedule(self, t: float, callback: Callable[..., None], *args: Any) -> EventHandle:
        """Schedule callback(*args) at absolute time t (>= now)."""
        if t < self.now - 1e-9:
            raise ValueError(f"cannot schedule into the past: t={t} < now={self.now}")
        self._seq += 1
        heapq.heappush(self._heap, (max(t, self.now), self._seq, callback, args))
        return EventHandle(t=t, seq=self._seq)

    def after(self, dt: float, callback: Callable[..., None], *args: Any) -> EventHandle:
        return self.schedule(self.now + dt, callback, *args)

    def cancel(self, handle: EventHandle) -> None:
        self._cancelled.add(handle.seq)

    def run(self, until_s: float) -> None:
        """Execute events in (t, seq) order until the clock passes until_s or the heap drains."""
        while self._heap:
            t, seq, cb, args = self._heap[0]
            if t > until_s:
                break
            heapq.heappop(self._heap)
            if seq in self._cancelled:
                self._cancelled.discard(seq)
                continue
            self.now = t
            cb(*args)
            self.events_executed += 1
        self.now = max(self.now, until_s)
