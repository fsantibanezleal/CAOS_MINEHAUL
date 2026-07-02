"""Speed-restricted zones: dynamic areal overlays composing by MIN, never touching frozen Segments.

Grounded in practice (docs/frameworks refs: Morenci in-pit driving standard, QLD RS19): sites run a
global cap plus POSTED lower-limit zones near infrastructure, active dumps/shovels, under walls with
active TARPs, and in dust/rain/visibility events. Zones appear/disappear with events; the SEGMENT
design limit stays static beneath the overlay. Deliberately distinct from constraints.DirectionZone
(a traffic-arbitration RESOURCE); a SpeedZone is a pure speed overlay.

Composition: per-segment cap = MIN over all zones containing the segment (+ `extra` pairs, e.g.
damage derations). The segment's own limit is NOT applied here — Router/DES already take
min(segment.speed_limit_kmh, cap) at the call site, so a cap above the limit is naturally a no-op.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum


class ZoneReason(str, Enum):
    INFRASTRUCTURE = "infrastructure"
    DUST_VISIBILITY = "dust_visibility"
    SLOPE_DAMAGE = "slope_damage"
    WET_ROAD = "wet_road"
    CUSTOM = "custom"


@dataclass(frozen=True)
class SpeedZone:
    id: int
    name: str
    segment_ids: tuple[int, ...]
    cap_kmh: float
    reason: ZoneReason = ZoneReason.CUSTOM

    def __post_init__(self) -> None:
        if self.cap_kmh <= 0:
            raise ValueError(f"zone {self.id}: cap must be > 0 (a 0 cap is a CLOSURE, not a zone)")
        if not self.segment_ids:
            raise ValueError(f"zone {self.id}: empty segment membership")

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "segment_ids": list(self.segment_ids),
                "cap_kmh": self.cap_kmh, "reason": self.reason.value}

    @classmethod
    def from_dict(cls, d: dict) -> "SpeedZone":
        return cls(int(d["id"]), str(d["name"]), tuple(int(x) for x in d["segment_ids"]),
                   float(d["cap_kmh"]), ZoneReason(d.get("reason", "custom")))


def compose_speed_caps(zones: Iterable[SpeedZone],
                       extra: Mapping[int, float] | None = None) -> dict[int, float]:
    """Per-segment cap = MIN over all zones containing the segment (and `extra` deration pairs)."""
    caps: dict[int, float] = {}
    for z in zones:
        for sid in z.segment_ids:
            cur = caps.get(sid)
            caps[sid] = z.cap_kmh if cur is None else min(cur, z.cap_kmh)
    for sid, cap in (extra or {}).items():
        cur = caps.get(sid)
        caps[sid] = cap if cur is None else min(cur, cap)
    return caps
