"""
Event layer: derives operational events from tracking results.

A pure, self-contained module above the tracker. It depends only on the
tracking value types — no database, web, or OpenCV imports — so it stays
reusable and testable in isolation. Persistence and live delivery are layered
on top by the backend.
"""

from .config import EventEngineConfig
from .detectors import (
    AppearanceDetector,
    EventDetector,
    FrameContext,
    NearCollisionDetector,
    StationaryDetector,
    TrackMeta,
)
from .engine import EventEngine, default_detectors
from .event_types import Event, EventType, Severity
from .geometry import (
    Point,
    crossing_sign,
    point_in_polygon,
    segments_intersect,
    side,
)
from .geometry_detectors import LineCrossingDetector, ZoneDetector
from .regions import CrossingLine, Zone

__all__ = [
    # Engine + config
    "EventEngine",
    "EventEngineConfig",
    "default_detectors",
    # Value types
    "Event",
    "EventType",
    "Severity",
    # Regions / geometry config
    "Zone",
    "CrossingLine",
    "Point",
    # Detectors
    "EventDetector",
    "FrameContext",
    "TrackMeta",
    "AppearanceDetector",
    "StationaryDetector",
    "NearCollisionDetector",
    "ZoneDetector",
    "LineCrossingDetector",
    # Geometry primitives
    "point_in_polygon",
    "segments_intersect",
    "crossing_sign",
    "side",
]
