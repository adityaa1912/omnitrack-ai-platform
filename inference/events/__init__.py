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

__all__ = [
    "EventEngine",
    "EventEngineConfig",
    "default_detectors",
    "Event",
    "EventType",
    "Severity",
    "EventDetector",
    "FrameContext",
    "TrackMeta",
    "AppearanceDetector",
    "StationaryDetector",
    "NearCollisionDetector",
]
