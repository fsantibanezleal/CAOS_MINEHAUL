"""Mine-planning layer: immutable pit models + plans, and the mutable mining-progression state."""
from .phase import MinePlan, Period, Phase
from .pit_model import Bench, DigBlock, PitModel
from .damage import DamageConfig, DamageEffects, DamageSeverity, SlopeDamageEvent, resolve_damage
from .overlay import NetworkOverlay
from .state import BlockCompletedError, DepletionResult, FaceStatus, PitState, PlanOrderError
from .zones import SpeedZone, ZoneReason, compose_speed_caps

__all__ = ["PitModel", "Bench", "DigBlock", "Phase", "Period", "MinePlan",
           "PitState", "DepletionResult", "FaceStatus", "PlanOrderError", "BlockCompletedError",
           "NetworkOverlay", "SpeedZone", "ZoneReason", "compose_speed_caps",
           "SlopeDamageEvent", "DamageSeverity", "DamageConfig", "DamageEffects", "resolve_damage"]
