"""Geometry layer: parametric mine solids (open pit U8; underground U10) built as pure functions
of a frozen design document. No randomness lives here — generators (scenarios/) sample designs."""
from .openpit import (OpenPitDesign, OpenPitGeometry, PitGeometryError, RimShape,
                      build_open_pit)
from .paths import horizontal_length, polyline_length, signed_grade_pct
from .underground import (DriftSpec, LevelSpec, OrePassSpec, UndergroundDesign,
                          UndergroundGeometry, UndergroundGeometryError, build_underground)

__all__ = [
    "OpenPitDesign", "OpenPitGeometry", "PitGeometryError", "RimShape", "build_open_pit",
    "polyline_length", "horizontal_length", "signed_grade_pct",
    "UndergroundDesign", "UndergroundGeometry", "UndergroundGeometryError", "build_underground",
    "DriftSpec", "LevelSpec", "OrePassSpec",
]
