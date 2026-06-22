"""
Core value types for the event layer.

Pure data definitions with no dependency on tracking, persistence, or the web
layer. Everything here is serialization-friendly so the same `Event` can flow
unchanged from the engine into the database and out over the wire in later
commits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class EventType(str, Enum):
    """
    The full taxonomy of events the engine can emit.

    String-valued so it serializes cleanly to JSON and to a SQL column without
    a custom encoder. The four geometry-dependent types (ENTERED, EXITED,
    CROSSING_DIRECTION, DWELL_TIME) are declared here for a stable contract but
    are not produced until the geometry detectors land in a later commit.
    """

    OBJECT_APPEARED = "object_appeared"
    OBJECT_DISAPPEARED = "object_disappeared"
    OBJECT_ENTERED = "object_entered"
    OBJECT_EXITED = "object_exited"
    CROSSING_DIRECTION = "crossing_direction"
    DWELL_TIME = "dwell_time"
    STATIONARY_OBJECT = "stationary_object"
    NEAR_COLLISION = "near_collision"


# Severity ranks, kept out of the Enum body so the string value stays the only
# wire representation while ordering is still available for sorting/filtering.
_SEVERITY_RANKS: dict[str, int] = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


class Severity(str, Enum):
    """Ordered severity levels. Use `.rank` for comparison/sorting."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Monotonic integer rank (INFO=0 .. CRITICAL=4) for ordering."""
        return _SEVERITY_RANKS[self.value]


@dataclass(frozen=True)
class Event:
    """
    A single derived event.

    Immutable by design: once a detector emits an event it is a historical
    fact and must not be mutated downstream. `metadata` carries detector-
    specific context (e.g. displacement, partner track, dwell seconds) without
    widening the core schema.

    Coordinates and timestamps are sourced from the originating frame, not
    wall-clock, so events line up exactly with the tracking data they derive
    from.
    """

    stream_id: str
    event_type: EventType
    severity: Severity
    frame_id: int
    timestamp: float
    track_id: Optional[int] = None
    class_name: Optional[str] = None
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain JSON-friendly dict (used by persistence/API later)."""
        return {
            "stream_id": self.stream_id,
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "severity_rank": self.severity.rank,
            "frame_id": self.frame_id,
            "timestamp": self.timestamp,
            "track_id": self.track_id,
            "class_name": self.class_name,
            "message": self.message,
            "metadata": dict(self.metadata),
        }
