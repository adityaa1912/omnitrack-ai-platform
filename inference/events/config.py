"""
Tunable configuration for the event engine.

Only the parameters needed by the config-free detectors (stationary, near
collision) are present in this commit. Zone and line geometry for the
entered/exited/crossing/dwell detectors are added in a later commit; this
dataclass is the single extension point for them.

Every threshold uses hysteresis where relevant: a tighter value to *raise* an
event and a looser value to *clear* it. This prevents an object hovering at the
boundary from emitting a storm of duplicate events.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EventEngineConfig:
    """Thresholds governing event derivation. All distances are in pixels."""

    enabled: bool = True

    # --- Stationary object ---
    # An active track whose recent trajectory stays within
    # `stationary_max_spread_px` for at least `stationary_min_frames` samples is
    # reported stationary. It is re-armed (allowed to fire again) only after its
    # spread exceeds `stationary_clear_spread_px`.
    stationary_min_frames: int = 30
    stationary_max_spread_px: float = 8.0
    stationary_clear_spread_px: float = 16.0

    # --- Near collision ---
    # Two active tracks whose centroids come within
    # `near_collision_distance_px` raise one CRITICAL event for the pair. The
    # pair is re-armed only after they separate beyond
    # `near_collision_clear_distance_px`.
    near_collision_distance_px: float = 60.0
    near_collision_clear_distance_px: float = 90.0

    def __post_init__(self) -> None:
        if self.stationary_clear_spread_px < self.stationary_max_spread_px:
            raise ValueError(
                "stationary_clear_spread_px must be >= stationary_max_spread_px "
                "for stable hysteresis"
            )
        if self.near_collision_clear_distance_px < self.near_collision_distance_px:
            raise ValueError(
                "near_collision_clear_distance_px must be >= "
                "near_collision_distance_px for stable hysteresis"
            )
        if self.stationary_min_frames < 2:
            raise ValueError("stationary_min_frames must be >= 2")
