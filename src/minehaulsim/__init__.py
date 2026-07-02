"""minehaulsim — deterministic DES of open-pit + underground mine haulage on constrained road networks.

Public API grows per build unit; see CHANGELOG.md. Display version lives in VERSION (X.XX.XXX).
"""
from .rng import RngManager
from .types import XYZ, CycleEvent, MineKind, SiteKind, dist3

__version__ = "0.1.0"  # PEP 440; display form in VERSION
__all__ = ["RngManager", "XYZ", "CycleEvent", "MineKind", "SiteKind", "dist3", "__version__"]
