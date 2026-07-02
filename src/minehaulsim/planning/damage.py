"""Slope damage: wall events resolved to network effects (closures + derations + exclusion zones).

Grounded in TARP practice (the Bingham Canyon Slope Stability 2020 tables are the reference in the
research doc): escalation levels pause/derate mining under a wall, activate an EXCLUSION ZONE over
the runout footprint, and can close the haul road below. Our v1 severity ladder maps the practice:

    TENSION_CRACKS  monitor      derate the footprint to cap_monitor (30 km/h); buffer untouched
    RAVELING        L2-ish       close bench-floor segments in the span; derate the rest to 20; buffer 30
    WEDGE_FAILURE   L3-ish       close the runout footprint; derate the buffer to 20
    WALL_COLLAPSE   L4-ish       close footprint AND buffer (full exclusion)

Resolution geometry is pure + deterministic: the FOOTPRINT is the prism under the wall span (the xy
band of the crest span dilated by margin_m, elevations from the crest down `depth_benches`); a
segment is affected iff ANY of its polyline vertices falls inside (sufficient because generators
emit piecewise-short polylines; documented). The BUFFER dilates the footprint by buffer_m.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from ..network.graph import RoadNetwork
from .pit_model import PitModel
from .zones import SpeedZone, ZoneReason


class DamageSeverity(str, Enum):
    TENSION_CRACKS = "tension_cracks"
    RAVELING = "raveling"
    WEDGE_FAILURE = "wedge_failure"
    WALL_COLLAPSE = "wall_collapse"


@dataclass(frozen=True)
class DamageConfig:
    margin_m: float = 10.0
    buffer_m: float = 60.0
    cap_monitor_kmh: float = 30.0
    cap_derate_kmh: float = 20.0
    cap_buffer_kmh: float = 30.0


@dataclass(frozen=True)
class SlopeDamageEvent:
    id: int
    bench_id: int                 # crest bench of the affected wall section
    arc_s0: float
    arc_s1: float                 # affected span along the bench polyline [m]
    severity: DamageSeverity
    depth_benches: int = 1        # runout reach below the crest

    def __post_init__(self) -> None:
        if self.arc_s1 <= self.arc_s0:
            raise ValueError(f"damage {self.id}: arc_s1 must exceed arc_s0")
        if self.depth_benches < 0:
            raise ValueError(f"damage {self.id}: depth_benches must be >= 0")

    def to_dict(self) -> dict:
        return {"id": self.id, "bench_id": self.bench_id, "arc_s0": self.arc_s0,
                "arc_s1": self.arc_s1, "severity": self.severity.value,
                "depth_benches": self.depth_benches}

    @classmethod
    def from_dict(cls, d: dict) -> "SlopeDamageEvent":
        return cls(int(d["id"]), int(d["bench_id"]), float(d["arc_s0"]), float(d["arc_s1"]),
                   DamageSeverity(d["severity"]), int(d.get("depth_benches", 1)))


@dataclass(frozen=True)
class DamageEffects:
    event_id: int
    closed_segments: frozenset[int]
    derated: tuple[tuple[int, float], ...]     # (segment_id, cap_kmh), sorted
    exclusion_zone: SpeedZone | None


def _span_band(model: PitModel, event: SlopeDamageEvent, margin: float) -> tuple[np.ndarray, float, float, float]:
    """(crest span points xy (k,2), z_lo, z_hi, xy dilation base) of the affected wall prism."""
    be = model.bench(event.bench_id)
    pts = be.polyline
    d = np.diff(pts, axis=0)
    seg_len = np.sqrt((d * d).sum(axis=1))
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    n = 24
    ss = np.linspace(max(0.0, event.arc_s0), min(cum[-1], event.arc_s1), n)
    span = np.empty((n, 2))
    for k, s in enumerate(ss):
        i = min(int(np.searchsorted(cum, s, side="right")) - 1, len(seg_len) - 1)
        t = 0.0 if seg_len[i] <= 0 else (s - cum[i]) / seg_len[i]
        p = pts[i] + t * d[i]
        span[k] = p[:2]
    z_hi = be.z + margin
    z_lo = be.z - event.depth_benches * be.height_m - margin
    return span, z_lo, z_hi, margin


def _segments_within(net: RoadNetwork, span_xy: np.ndarray, z_lo: float, z_hi: float,
                     dist_m: float) -> set[int]:
    """Segments with ANY polyline vertex inside the prism (xy within dist_m of the span, z in range)."""
    out: set[int] = set()
    for seg in net.segments.values():
        v = seg.polyline
        zok = (v[:, 2] >= z_lo) & (v[:, 2] <= z_hi)
        if not zok.any():
            continue
        # xy distance of each in-z vertex to the span point set
        vv = v[zok][:, :2]
        d2 = ((vv[:, None, :] - span_xy[None, :, :]) ** 2).sum(axis=2)
        if (d2.min(axis=1) <= dist_m * dist_m).any():
            out.add(seg.id)
    return out


def resolve_damage(model: PitModel, net: RoadNetwork, event: SlopeDamageEvent,
                   cfg: DamageConfig = DamageConfig()) -> DamageEffects:
    """Pure + deterministic resolution of a wall event into closures/derations/an exclusion zone."""
    span, z_lo, z_hi, _ = _span_band(model, event, cfg.margin_m)
    footprint = _segments_within(net, span, z_lo, z_hi, cfg.margin_m)
    buffer_all = _segments_within(net, span, z_lo - cfg.buffer_m, z_hi + cfg.buffer_m, cfg.buffer_m)
    buffer = buffer_all - footprint

    closed: set[int] = set()
    derated: dict[int, float] = {}
    sev = event.severity
    if sev is DamageSeverity.TENSION_CRACKS:
        for s in footprint:
            derated[s] = cfg.cap_monitor_kmh
    elif sev is DamageSeverity.RAVELING:
        bench_z = model.bench(event.bench_id).z
        for s in footprint:
            # bench-floor segments in the span close; the rest of the footprint derates
            seg = net.segments[s]
            if abs(float(seg.polyline[:, 2].mean()) - bench_z) <= cfg.margin_m:
                closed.add(s)
            else:
                derated[s] = cfg.cap_derate_kmh
        for s in buffer:
            derated[s] = cfg.cap_buffer_kmh
    elif sev is DamageSeverity.WEDGE_FAILURE:
        closed |= footprint
        for s in buffer:
            derated[s] = cfg.cap_derate_kmh
    elif sev is DamageSeverity.WALL_COLLAPSE:
        closed |= footprint | buffer

    derated_pairs = tuple(sorted((s, c) for s, c in derated.items() if s not in closed))
    zone = None
    if derated_pairs:
        zone = SpeedZone(id=900_000 + event.id, name=f"damage-{event.id}",
                         segment_ids=tuple(s for s, _ in derated_pairs),
                         cap_kmh=min(c for _, c in derated_pairs), reason=ZoneReason.SLOPE_DAMAGE)
    return DamageEffects(event_id=event.id, closed_segments=frozenset(closed),
                         derated=derated_pairs, exclusion_zone=zone)
