from dataclasses import dataclass, field
from collections import deque
from typing import Optional
import numpy as np

from .types import Detection


@dataclass
class TrajectoryPoint:
    """Single point in an object's trajectory."""
    frame_id: int
    x: float  # Center X coordinate
    y: float  # Center Y coordinate
    timestamp: float


@dataclass
class TrackedObject:
    """
    Object with persistent tracking ID across frames.

    Maintains detection history and trajectory for visualization
    and future anomaly detection.
    """
    track_id: int
    detections: deque = field(default_factory=lambda: deque(maxlen=30))
    trajectory_points: deque = field(default_factory=lambda: deque(maxlen=100))
    first_seen_frame: int = 0
    last_seen_frame: int = 0
    times_disappeared: int = 0

    def __post_init__(self):
        """Initialize deques with proper max lengths if not provided."""
        if not isinstance(self.detections, deque):
            self.detections = deque(self.detections, maxlen=30)
        if not isinstance(self.trajectory_points, deque):
            self.trajectory_points = deque(self.trajectory_points, maxlen=100)

    @property
    def current_detection(self) -> Optional[Detection]:
        """Get most recent detection for this track."""
        return self.detections[-1] if len(self.detections) > 0 else None

    @property
    def age(self) -> int:
        """Frames since first detection."""
        return self.last_seen_frame - self.first_seen_frame + 1

    @property
    def is_lost(self) -> bool:
        """Whether track is currently unmatched (lost)."""
        return self.times_disappeared > 0

    def add_detection(self, detection: Detection, frame_id: int, timestamp: float) -> None:
        """Add detection and trajectory point."""
        self.detections.append(detection)
        self.last_seen_frame = frame_id
        self.times_disappeared = 0

        x_center = (detection.x1 + detection.x2) / 2
        y_center = (detection.y1 + detection.y2) / 2

        self.trajectory_points.append(
            TrajectoryPoint(
                frame_id=frame_id,
                x=x_center,
                y=y_center,
                timestamp=timestamp,
            )
        )

    def mark_disappeared(self) -> None:
        """Increment disappearance counter (track lost for one frame)."""
        self.times_disappeared += 1

    def get_trajectory_array(self) -> Optional[np.ndarray]:
        """
        Get trajectory points as array for efficient rendering.

        Returns:
            (N, 2) array of [x, y] coordinates, or None if no trajectory
        """
        if len(self.trajectory_points) < 2:
            return None

        points = [(p.x, p.y) for p in self.trajectory_points]
        return np.array(points, dtype=np.float32)


@dataclass
class TrackingResult:
    """Result of tracking update for a frame."""
    frame_id: int
    tracked_objects: list[TrackedObject] = field(default_factory=list)
    active_tracks: int = 0
    lost_tracks: int = 0
    tracking_time_ms: float = 0.0

    @property
    def total_tracks(self) -> int:
        """Total number of tracked objects."""
        return len(self.tracked_objects)

    def get_active_objects(self) -> list[TrackedObject]:
        """Get only objects with current detections (not lost)."""
        return [obj for obj in self.tracked_objects if obj.current_detection is not None]

    def get_lost_objects(self) -> list[TrackedObject]:
        """Get only objects that are currently lost (awaiting redetection)."""
        return [obj for obj in self.tracked_objects if obj.is_lost]
