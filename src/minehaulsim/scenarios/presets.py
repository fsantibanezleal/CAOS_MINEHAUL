"""Named preset scenarios: fixed (params, seed) pairs that regenerate identically anywhere.

Presets are entry points for examples, docs and the CLI — each exercises a different structural
class of pit so a consumer sees the variety without writing sampler code.
"""
from __future__ import annotations

from .openpit_gen import OpenPitParams, generate_open_pit
from .spec import MineSpec

PRESETS: dict[str, tuple[OpenPitParams, int]] = {
    # small training pit: shallow spiral, one crusher + one dump, compact fleet
    "starter_pit": (OpenPitParams(
        name="starter_pit", n_benches=7, ramp_style="spiral", n_shovels=2,
        n_crushers=1, n_waste_dumps=1, stockpile=False, n_surface_junctions=1), 11),
    # deep single-lane spiral: DirectionZones on the ramp, long cycles
    "deep_spiral": (OpenPitParams(
        name="deep_spiral", n_benches=16, ramp_style="spiral", ramp_lanes=1,
        zone_policy="loaded_priority"), 23),
    # switchback wall: 180-degree turn junctions, slow ramp speeds
    "switchback_ridge": (OpenPitParams(
        name="switchback_ridge", n_benches=10, ramp_style="switchback"), 37),
    # two ramps, one-way circulation, multi-phase expanded rim, big mixed fleet
    "twin_ramp_expansion": (OpenPitParams(
        name="twin_ramp_expansion", ramp_style="dual_spiral", ramp_lanes=1, n_phases=3,
        n_shovels=6), 53),
}


def preset_names() -> list[str]:
    return sorted(PRESETS)


def load_preset(name: str) -> MineSpec:
    """Regenerate a preset scenario (deterministic: same spec bytes every time)."""
    if name not in PRESETS:
        raise KeyError(f"unknown preset {name!r}; available: {preset_names()}")
    params, seed = PRESETS[name]
    return generate_open_pit(params, seed=seed)
