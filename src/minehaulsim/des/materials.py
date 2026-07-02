"""Material-flow state: the objects that COUPLE the two underground fleets.

OrePassRuntime — a finite-capacity vertical inventory. LHD tips ADD tonnes at the tip; the
haulage-level chute SUBTRACTS them when it loads a truck. A full pass blocks the LHD at the tip;
an empty pass parks the loading truck under the chute. Conservation is a tested invariant:
    tipped_t == chuted_t + level_t   (exactly, every event)

ShaftBinRuntime — the shaft option's dump target: a bin drained continuously by hoisting at
`hoist_tph`. Dumping needs headroom; the required wait for space is CLOSED-FORM (deterministic),
never polled.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OrePassRuntime:
    pass_id: int
    chute_node: int
    capacity_t: float
    level_t: float = 0.0
    tipped_t: float = 0.0
    chuted_t: float = 0.0

    def can_tip(self, tonnes: float) -> bool:
        return self.level_t + tonnes <= self.capacity_t + 1e-9

    def tip(self, tonnes: float) -> None:
        if not self.can_tip(tonnes):
            raise RuntimeError(f"ore pass {self.pass_id} overfull")
        self.level_t += tonnes
        self.tipped_t += tonnes

    def can_draw(self, tonnes: float) -> bool:
        return self.level_t >= tonnes - 1e-9

    def draw(self, tonnes: float) -> None:
        if not self.can_draw(tonnes):
            raise RuntimeError(f"ore pass {self.pass_id} underflow")
        self.level_t -= tonnes
        self.chuted_t += tonnes

    def summary(self) -> dict:
        return {"tipped_t": round(self.tipped_t, 6), "chuted_t": round(self.chuted_t, 6),
                "inventory_t": round(self.level_t, 6)}


@dataclass
class ShaftBinRuntime:
    node: int
    capacity_t: float
    hoist_tph: float
    level_t: float = 0.0
    t_ref: float = 0.0
    hoisted_t: float = field(default=0.0)

    def _drain_to(self, now: float) -> None:
        drained = min(self.level_t, self.hoist_tph / 3600.0 * max(0.0, now - self.t_ref))
        self.level_t -= drained
        self.hoisted_t += drained
        self.t_ref = now

    def wait_for_space_s(self, now: float, tonnes: float) -> float:
        """0.0 when the dump fits now; else the exact seconds of hoisting needed to make room."""
        self._drain_to(now)
        overflow = self.level_t + tonnes - self.capacity_t
        if overflow <= 1e-9:
            return 0.0
        return overflow / (self.hoist_tph / 3600.0)

    def dump(self, now: float, tonnes: float) -> None:
        self._drain_to(now)
        if self.level_t + tonnes > self.capacity_t + 1e-9:
            raise RuntimeError("shaft bin overfull (dump granted without space)")
        self.level_t += tonnes

    def summary(self, now: float) -> dict:
        self._drain_to(now)
        return {"hoisted_t": round(self.hoisted_t, 6), "bin_level_t": round(self.level_t, 6)}
