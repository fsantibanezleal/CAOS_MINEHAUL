"""minehaulsim — deterministic DES of open-pit + underground mine haulage on constrained road networks.

Public API grows per build unit; see CHANGELOG.md. Display version lives in VERSION (X.XX.XXX).
"""
__version__ = "0.2.0"  # PEP 440; display form in VERSION. MUST bind before submodule imports:
                       # io.provenance reads it during package initialization.

from .rng import RngManager                                                    # noqa: E402
from .scenarios import (MineSpec, OpenPitParams, generate_batch,               # noqa: E402
                        generate_open_pit, load_preset, preset_names, validate_spec)
from .types import XYZ, CycleEvent, MineKind, SiteKind, dist3                  # noqa: E402
__all__ = [
    "RngManager", "XYZ", "CycleEvent", "MineKind", "SiteKind", "dist3", "__version__",
    "MineSpec", "OpenPitParams", "generate_open_pit", "generate_batch",
    "load_preset", "preset_names", "validate_spec",
]
