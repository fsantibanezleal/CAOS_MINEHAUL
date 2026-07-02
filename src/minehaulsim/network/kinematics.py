"""Speed-by-grade kinematics: the rimpull/retarder solver + segment traversal times.

The TALPAC/FPC-standard model:
    Effective resistance [%] = grade_pct (signed, + uphill in travel direction) + rolling_pct.
    Required force at constant speed:  F_req [kN] = GVW_t * g * (effective_resistance / 100).
    UPHILL / positive resistance: attainable speed = the largest v with rimpull(v) >= F_req.
    DOWNHILL (negative effective resistance): the truck must HOLD the descent — attainable speed =
    the largest v with retarder(v) >= |F_req| (dynamic-brake absorption limits descent speed).
    Final segment speed = min(attainable, segment speed limit, class max speed).

Traversal time adds a bounded trapezoidal acceleration penalty:
    t = length / v_seg + max(0, v_seg - v_entry) / a,   a = 0.35 m/s^2 loaded, 0.5 empty.
`v_entry` is the previous segment's speed (a junction stop resets it to 0), so ramp climbs out of
junctions are honestly slower than free-flow.

Scalability: `SpeedSolver` memoizes on (class, GVW bucket, grade key, rr, limit) — the event loop
never solves a curve; hot lookups are O(1) dict hits. ~12 GVW buckets between empty and max
payload bound the cache size per class.
"""
from __future__ import annotations

from ..equipment.catalog import G, LhdClass, TruckClass

N_GVW_BUCKETS = 12


def _interp_curve_speed(curve: tuple[tuple[float, float], ...], f_req_kn: float) -> float:
    """Largest speed [km/h] at which the (decreasing) force curve still meets f_req_kn.

    Walks the sampled envelope from fast to slow; linear interpolation between the bracketing
    points. Below the slowest sampled point the machine cannot meet the demand -> 0.0 (stall).
    """
    if f_req_kn <= 0:
        return curve[-1][0]
    # curve is increasing in F as v decreases; find the fastest v with F(v) >= f_req
    prev_v, prev_f = None, None
    for v_kmh, f_kn in reversed(curve):          # fast -> slow
        if f_kn >= f_req_kn:
            if prev_f is None:
                return v_kmh                      # even the fastest sampled speed meets it
            # interpolate between (v_kmh, f_kn) and the faster (prev_v, prev_f)
            span = f_kn - prev_f
            t = 0.0 if span <= 0 else (f_req_kn - prev_f) / span
            return prev_v + (v_kmh - prev_v) * t
        prev_v, prev_f = v_kmh, f_kn
    return 0.0


def attainable_speed_kmh(unit: TruckClass | LhdClass, gvw_t: float, grade_pct: float,
                         rolling_pct: float) -> float:
    """Curve-limited speed for a unit at GVW on a segment of signed grade + rolling resistance."""
    eff = grade_pct + rolling_pct
    f_req = gvw_t * G * abs(eff) / 100.0
    if eff >= 0:
        return _interp_curve_speed(unit.rimpull_kn, f_req)
    return _interp_curve_speed(unit.retarder_kn, f_req)


class SpeedSolver:
    """Memoized speed lookups per (unit, GVW bucket, grade, rr, limit) — the event-loop-safe API."""

    def __init__(self) -> None:
        self._cache: dict[tuple, float] = {}

    @staticmethod
    def gvw_bucket(unit: TruckClass | LhdClass, gvw_t: float) -> int:
        """Bucket index 0..N-1 between empty and max-plausible GVW (bounds the cache)."""
        payload_max = getattr(unit, "payload_mean_t", getattr(unit, "bucket_t", 0.0)) + \
            3 * getattr(unit, "payload_sd_t", getattr(unit, "bucket_sd_t", 0.0))
        lo, hi = unit.empty_t, unit.empty_t + max(1e-9, payload_max)
        f = (min(max(gvw_t, lo), hi) - lo) / (hi - lo)
        return min(N_GVW_BUCKETS - 1, int(f * N_GVW_BUCKETS))

    def speed_ms(self, unit: TruckClass | LhdClass, gvw_t: float, grade_pct: float,
                 rolling_pct: float, limit_kmh: float) -> float:
        b = self.gvw_bucket(unit, gvw_t)
        key = (unit.name, b, round(grade_pct, 2), round(rolling_pct, 2), round(limit_kmh, 1))
        v = self._cache.get(key)
        if v is None:
            # solve at the bucket's representative GVW (its midpoint) for stability across draws
            payload_max = getattr(unit, "payload_mean_t", getattr(unit, "bucket_t", 0.0)) + \
                3 * getattr(unit, "payload_sd_t", getattr(unit, "bucket_sd_t", 0.0))
            lo, hi = unit.empty_t, unit.empty_t + max(1e-9, payload_max)
            gvw_rep = lo + (b + 0.5) / N_GVW_BUCKETS * (hi - lo)
            v_kmh = min(attainable_speed_kmh(unit, gvw_rep, grade_pct, rolling_pct),
                        limit_kmh, unit.max_speed_kmh)
            v = max(0.0, v_kmh) / 3.6
            self._cache[key] = v
        return v


ACCEL_LOADED_MS2 = 0.35
ACCEL_EMPTY_MS2 = 0.5


def traverse_time_s(length_m: float, v_seg_ms: float, v_entry_ms: float, loaded: bool) -> float:
    """Segment traversal time with the bounded trapezoidal acceleration penalty (module docstring)."""
    if v_seg_ms <= 0:
        raise ValueError("stalled: v_seg <= 0 (grade exceeds the unit's curve capability)")
    a = ACCEL_LOADED_MS2 if loaded else ACCEL_EMPTY_MS2
    t_acc = max(0.0, v_seg_ms - max(0.0, v_entry_ms)) / a
    return length_m / v_seg_ms + t_acc
