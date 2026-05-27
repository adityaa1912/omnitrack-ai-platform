from dataclasses import dataclass, field
from typing import Optional
import numpy as np


@dataclass
class Frame:
    """Raw frame with metadata for tracking through pipeline."""
    timestamp: float
    frame_id: int
    data: np.ndarray

    def shape(self) -> tuple[int, int, int]:
        """Return frame shape (height, width, channels)."""
        return self.data.shape


@dataclass
class Detection:
    """Single object detection."""
    x1: float
    y1: float
    x2: float
    y2: float
    class_id: int
    confidence: float
    class_name: str
    track_id: Optional[int] = None

    def center(self) -> tuple[float, float]:
        """Return bounding box center (x, y)."""
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    def area(self) -> float:
        """Return bounding box area in pixels."""
        return (self.x2 - self.x1) * (self.y2 - self.y1)

    def as_tuple(self) -> tuple[float, float, float, float]:
        """Return coordinates as (x1, y1, x2, y2) for quick access."""
        return (self.x1, self.y1, self.x2, self.y2)


@dataclass
class InferenceResult:
    """Inference results for a single frame."""
    frame: Frame
    detections: list[Detection] = field(default_factory=list)
    inference_time_ms: float = 0.0
    model_name: str = "yolov8n"

    def __post_init__(self):
        """Ensure detections is a list."""
        if not isinstance(self.detections, list):
            self.detections = list(self.detections)

    @property
    def num_detections(self) -> int:
        return len(self.detections)

    def filter_by_confidence(self, threshold: float) -> list[Detection]:
        """Filter detections by confidence threshold."""
        return [d for d in self.detections if d.confidence >= threshold]
