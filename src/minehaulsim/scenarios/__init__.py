"""Scenario layer: frozen MineSpec documents, the varied generators, and the validity gates."""
from .geology import GEOLOGY_SCHEMA, attach_geology
from .openpit_gen import GenerationError, OpenPitParams, generate_batch, generate_open_pit
from .presets import PRESETS, load_preset, preset_names
from .spec import SPEC_SCHEMA, MineSpec, RuntimeBundle
from .underground_gen import (UndergroundParams, generate_underground,
                              generate_underground_batch)
from .validate import (CheckResult, ValidationReport, diversity_signature,
                       representative_cycle_s, static_match_factor, validate_spec)

__all__ = [
    "GEOLOGY_SCHEMA",
    "attach_geology",
    "MineSpec", "RuntimeBundle", "SPEC_SCHEMA",
    "OpenPitParams", "generate_open_pit", "generate_batch", "GenerationError",
    "UndergroundParams", "generate_underground", "generate_underground_batch",
    "PRESETS", "load_preset", "preset_names",
    "validate_spec", "ValidationReport", "CheckResult", "diversity_signature",
    "representative_cycle_s", "static_match_factor",
]
