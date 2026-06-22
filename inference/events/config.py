"""
Tunable configuration for the event engine.

Holds the thresholds for the config-free detectors (stationary, near collision)
and the scene geometry (zones, crossing lines, dwell threshold) consumed by the
geometry detectors. This dataclass is the single extension point for new
detector parameters.

Every threshold uses hysteresis where relevant: a tighter value to *raise* an
event and a looser value to *clear* it. This prevents an object hovering at the
boundary from emitting a storm of duplicate events.
"""

from __future__ import annotations

from dataclasses import dataclass

from .regions import CrossingLine, Zone


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

    # --- Scene geometry (zone & line detectors) ---
    # When empty (the default) the geometry detectors are inert, so an
    # unconfigured stream behaves exactly as before this commit.
    zones: tuple[Zone, ...] = ()
    lines: tuple[CrossingLine, ...] = ()

    # A track continuously inside a zone for at least `dwell_seconds`
    # (measured on source-frame timestamps) raises one DWELL_TIME event.
    dwell_seconds: float = 5.0

    def __post_init__(self) -> None:
        # Coerce region collections to immutable tuples for safe sharing.
        self.zones = tuple(self.zones)
        self.lines = tuple(self.lines)
        if self.dwell_seconds <= 0:
            raise ValueError("dwell_seconds must be > 0")
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
