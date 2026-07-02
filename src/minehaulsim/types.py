"""Core shared types: identifiers, enums and unit conventions used across every layer.

Unit conventions (binding, documented in docs/architecture/units.md):
    distance  metres [m]        grade     signed fraction (+0.10 = 10% uphill in travel direction)
    time      seconds [s]       speed     metres/second [m/s] (km/h only at I/O boundaries)
    mass      tonnes [t]        angles    degrees at spec boundaries, radians internally

Everything a user touches is a frozen dataclass with to_dict/from_dict (JSON round-trip).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

# --- identifiers (plain ints/strs, wrapped for readability) ---
NodeId = int          # a network node (junction, loader spot, dump spot, portal, shaft station)
SegmentId = int       # a directed road/drift segment
TruckId = int
LoaderId = int        # shovel / LHD / loading unit
DumpId = int


class SiteKind(str, Enum):
    """What a network node hosts."""
    JUNCTION = "junction"
    LOADER = "loader"          # shovel face / draw point
    DUMP = "dump"              # crusher, waste dump, stockpile, ore pass tip
    PORTAL = "portal"          # surface portal (underground mode)
    SHAFT = "shaft"            # shaft station (underground mode)


class CycleEvent(str, Enum):
    """cyclelog/v1 event tokens — each marks the START of its phase (DispatchLab contract)."""
    LOAD = "load"
    HAUL = "haul"
    DUMP = "dump"
    RETURN = "return"


class MineKind(str, Enum):
    OPEN_PIT = "open-pit"
    UNDERGROUND = "underground"


@dataclass(frozen=True)
class XYZ:
    """A point in mine world coordinates [m]; z is elevation (0 at surface/rim, negative down)."""
    x: float
    y: float
    z: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "XYZ":
        return cls(float(d["x"]), float(d["y"]), float(d["z"]))


def dist3(a: XYZ, b: XYZ) -> float:
    return ((b.x - a.x) ** 2 + (b.y - a.y) ** 2 + (b.z - a.z) ** 2) ** 0.5
