"""Equipment catalog: truck / loader / LHD classes with rimpull and retarder curves.

The bundled classes carry PUBLIC-SPEC-SHEET magnitudes (payloads, empty weights, engine power) and
HAND-AUTHORED performance curves generated from first principles below. They are documented as
**class-representative, not OEM-exact** (docs/data-contract.md): good enough that cycle times,
grade sensitivity and bunching behave like the real machine class, and honest about not being a
manufacturer's FPC table.

Curve generation (the physics, kept explicit so it can be audited):
    Rimpull at road speed v is limited by BOTH driveline power and tyre/traction:
        F(v) = min(F_traction,  eta * P / v)
    with eta the driveline efficiency (~0.85 mechanical), P rated power [W], v in [m/s], and
    F_traction ~ mu * W_drive (mu ~0.6 on maintained haul roads, W_drive the weight on driven
    axles ~55% of GVW for a rear-drive rigid truck). We sample that envelope at fixed speeds to
    produce the monotone-decreasing table the solver consumes.
    The retarder curve has the same 1/v shape from the retarder's power-absorption rating
    (~0.85 x engine power for modern electric-drive/retarder packages), capped by traction.

All masses in tonnes, speeds in km/h at this boundary (converted to SI inside kinematics).
"""
from __future__ import annotations

from dataclasses import dataclass, field

G = 9.80665  # m/s^2

# speeds [km/h] at which curve tables are sampled (dense at the low end where grades bite)
_CURVE_SPEEDS_KMH: tuple[float, ...] = (2.0, 3.0, 4.0, 6.0, 8.0, 11.0, 15.0, 20.0, 27.0, 35.0, 45.0, 55.0)


def _envelope_kn(power_kw: float, cap_kn: float, eta: float, speeds_kmh: tuple[float, ...]) -> tuple[tuple[float, float], ...]:
    """Sample F(v) = min(cap, eta*P/v) at the standard speeds -> ((v_kmh, F_kN), ...) monotone in v."""
    pts = []
    for v_kmh in speeds_kmh:
        v_ms = v_kmh / 3.6
        f_kn = min(cap_kn, (eta * power_kw) / v_ms)  # kW / (m/s) = kN
        pts.append((v_kmh, round(f_kn, 1)))
    return tuple(pts)


@dataclass(frozen=True)
class TruckClass:
    """A haul-truck class. `width_class` interacts with segment width (1 = single-lane vehicle)."""
    name: str
    kind: str                      # "rigid" | "articulated-ug"
    payload_mean_t: float
    payload_sd_t: float
    empty_t: float
    width_class: int
    power_kw: float
    max_speed_kmh: float
    rimpull_kn: tuple[tuple[float, float], ...]    # ((v_kmh, F_kN), ...) decreasing in v
    retarder_kn: tuple[tuple[float, float], ...]

    @property
    def gvw_max_t(self) -> float:
        return self.empty_t + self.payload_mean_t + 3 * self.payload_sd_t


@dataclass(frozen=True)
class LoaderClass:
    """A loading unit served by trucks (shovel / wheel loader). Loading = passes * pass_time."""
    name: str
    pass_t: float          # tonnes per pass (bucket)
    pass_time_s: float     # seconds per pass (swing cycle)
    time_cv: float         # lognormal coefficient of variation on the total load time
    spot_time_s: float     # truck spotting time at the face


@dataclass(frozen=True)
class LhdClass:
    """A load-haul-dump unit (underground): self-loads at a draw point, hauls, dumps at a pass/bin."""
    name: str
    bucket_t: float
    bucket_sd_t: float
    empty_t: float
    width_class: int
    power_kw: float
    max_speed_kmh: float
    dig_time_s: float      # mean self-load (dig) time
    dig_time_cv: float
    rimpull_kn: tuple[tuple[float, float], ...] = field(default=())
    retarder_kn: tuple[tuple[float, float], ...] = field(default=())


def _truck(name: str, kind: str, payload: float, sd: float, empty: float, width: int,
           power_kw: float, vmax: float) -> TruckClass:
    gvw = empty + payload                                # nominal loaded GVW [t]
    cap = 0.6 * 0.55 * gvw * G                           # traction cap [kN]: mu * drive-axle share * W
    return TruckClass(
        name=name, kind=kind, payload_mean_t=payload, payload_sd_t=sd, empty_t=empty,
        width_class=width, power_kw=power_kw, max_speed_kmh=vmax,
        rimpull_kn=_envelope_kn(power_kw, cap, 0.85, _CURVE_SPEEDS_KMH),
        retarder_kn=_envelope_kn(0.85 * power_kw, cap, 0.85, _CURVE_SPEEDS_KMH),
    )


def _lhd(name: str, bucket: float, sd: float, empty: float, power_kw: float, vmax: float,
         dig_s: float) -> LhdClass:
    gvw = empty + bucket
    cap = 0.65 * 0.9 * gvw * G                           # LHD: near-all weight on driven axles
    return LhdClass(
        name=name, bucket_t=bucket, bucket_sd_t=sd, empty_t=empty, width_class=1,
        power_kw=power_kw, max_speed_kmh=vmax, dig_time_s=dig_s, dig_time_cv=0.25,
        rimpull_kn=_envelope_kn(power_kw, cap, 0.8, _CURVE_SPEEDS_KMH),
        retarder_kn=_envelope_kn(0.8 * power_kw, cap, 0.8, _CURVE_SPEEDS_KMH),
    )


# ---- the bundled catalog (class-representative; see module docstring) ----
TRUCKS: dict[str, TruckClass] = {
    "CAT_777G": _truck("CAT_777G", "rigid", 98.0, 5.0, 74.0, 2, 765.0, 60.0),
    "CAT_785D": _truck("CAT_785D", "rigid", 140.0, 7.0, 102.0, 2, 1082.0, 55.0),
    "CAT_793F": _truck("CAT_793F", "rigid", 227.0, 11.0, 165.0, 2, 1976.0, 60.0),
    "UG_TRUCK_50": _truck("UG_TRUCK_50", "articulated-ug", 50.0, 3.0, 42.0, 1, 447.0, 40.0),
    "UG_TRUCK_63": _truck("UG_TRUCK_63", "articulated-ug", 63.0, 3.5, 52.0, 1, 567.0, 40.0),
}

LOADERS: dict[str, LoaderClass] = {
    "SHOVEL_25": LoaderClass("SHOVEL_25", pass_t=25.0, pass_time_s=35.0, time_cv=0.18, spot_time_s=30.0),
    "SHOVEL_45": LoaderClass("SHOVEL_45", pass_t=45.0, pass_time_s=38.0, time_cv=0.18, spot_time_s=30.0),
    "WHEEL_LOADER_18": LoaderClass("WHEEL_LOADER_18", pass_t=18.0, pass_time_s=42.0, time_cv=0.22, spot_time_s=25.0),
}

LHDS: dict[str, LhdClass] = {
    "LHD_14": _lhd("LHD_14", 14.0, 1.0, 38.0, 257.0, 25.0, 45.0),
    "LHD_18": _lhd("LHD_18", 18.0, 1.2, 45.0, 305.0, 25.0, 50.0),
}
