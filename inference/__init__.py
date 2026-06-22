"""Real-time YOLOv8 object detection pipeline with multi-object tracking."""

__version__ = "0.2.0"

from .config import AppConfig, DetectorConfig, FrameSourceConfig, VisualizerConfig
from .detector import Detector
from .events import Event, EventEngine, EventEngineConfig, EventType, Severity
from .frame_source import (
    FrameSource,
    WebcamFrameSource,
    VideoFileFrameSource,
    RTSPFrameSource,
    get_frame_source,
)
from .logger import get_logger, setup_logging
from .tracker import CentroidTracker, Tracker
from .tracking_config import TrackingConfig
from .tracking_types import TrackedObject, TrackingResult, TrajectoryPoint
from .types import Detection, Frame, InferenceResult
from .visualizer import FpsCounter, Visualizer

__all__ = [
    "AppConfig",
    "DetectorConfig",
    "FrameSourceConfig",
    "VisualizerConfig",
    "TrackingConfig",
    "Detector",
    "FrameSource",
    "WebcamFrameSource",
    "VideoFileFrameSource",
    "RTSPFrameSource",
    "get_frame_source",
    "Tracker",
    "CentroidTracker",
    "EventEngine",
    "EventEngineConfig",
    "Event",
    "EventType",
    "Severity",
    "Detection",
    "Frame",
    "InferenceResult",
    "TrackedObject",
    "TrackingResult",
    "TrajectoryPoint",
    "FpsCounter",
    "Visualizer",
    "get_logger",
    "setup_logging",
]
