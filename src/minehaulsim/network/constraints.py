"""Operational constraint models layered over the graph: DirectionZones and Junctions.

These are DATA here (immutable specs the generator emits); their runtime semantics (who waits,
when direction flips) are enforced by the DES resource layer (des/resources.py) which reads them.

DirectionZone — a chain of single-lane BIDIRECTIONAL segments between passing bays (an underground
drift between stockpiles/bays, a narrow one-lane pit ramp). At runtime it grants a DIRECTION:
vehicles traveling the active direction may enter (up to `max_in_zone`); opposing vehicles wait at
the boundary. Arbitration policies (the classic underground set, cf. Queen's 2016 traffic-sim work):
    lockout          strict direction mutual-exclusion, FIFO between direction groups
    loaded_priority  direction flips only when no LOADED vehicle still waits upstream
    group_batching   direction holds until k vehicles pass or a max-hold timer expires

Junction — a capacity-k conflict point where >= 3 used segments meet; crossing consumes the
junction for `cross_s` seconds, FIFO by arrival event order (deterministic).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ZonePolicy(str, Enum):
    LOCKOUT = "lockout"
    LOADED_PRIORITY = "loaded_priority"
    GROUP_BATCHING = "group_batching"


@dataclass(frozen=True)
class DirectionZone:
    id: int
    segment_ids: tuple[int, ...]        # the chained single-lane segments (order along the chain)
    policy: ZonePolicy = ZonePolicy.LOCKOUT
    max_in_zone: int = 4                # simultaneous same-direction vehicles allowed inside
    batch_k: int = 4                    # group_batching: flip after k vehicles
    max_hold_s: float = 480.0           # group_batching: or after this hold time

    def __post_init__(self) -> None:
        if not self.segment_ids:
            raise ValueError(f"zone {self.id}: empty segment chain")
        if self.max_in_zone < 1:
            raise ValueError(f"zone {self.id}: max_in_zone must be >= 1")

    def to_dict(self) -> dict:
        return {"id": self.id, "segment_ids": list(self.segment_ids), "policy": self.policy.value,
                "max_in_zone": self.max_in_zone, "batch_k": self.batch_k, "max_hold_s": self.max_hold_s}

    @classmethod
    def from_dict(cls, d: dict) -> "DirectionZone":
        return cls(id=int(d["id"]), segment_ids=tuple(int(x) for x in d["segment_ids"]),
                   policy=ZonePolicy(d.get("policy", "lockout")), max_in_zone=int(d.get("max_in_zone", 4)),
                   batch_k=int(d.get("batch_k", 4)), max_hold_s=float(d.get("max_hold_s", 480.0)))


@dataclass(frozen=True)
class Junction:
    id: int                             # the node id it guards
    capacity: int = 1
    cross_s: float = 12.0

    def __post_init__(self) -> None:
        if self.capacity < 1 or self.cross_s < 0:
            raise ValueError(f"junction {self.id}: bad capacity/cross_s")

    def to_dict(self) -> dict:
        return {"id": self.id, "capacity": self.capacity, "cross_s": self.cross_s}

    @classmethod
    def from_dict(cls, d: dict) -> "Junction":
        return cls(id=int(d["id"]), capacity=int(d.get("capacity", 1)), cross_s=float(d.get("cross_s", 12.0)))


# segment flow-control defaults (rule (ii) is what makes bunching EMERGE behind a slow truck)
HEADWAY_M_DEFAULT = 80.0    # capacity = max(1, floor(length / headway_m)) slots per direction
HEADWAY_S_DEFAULT = 8.0     # FIFO no-overtake: exit >= predecessor_exit + headway_s


def segment_capacity(length_m: float, headway_m: float = HEADWAY_M_DEFAULT) -> int:
    return max(1, int(length_m // headway_m))
