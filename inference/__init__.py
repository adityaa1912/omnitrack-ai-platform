"""Real-time YOLOv8 object detection pipeline with multi-object tracking."""

__version__ = "0.2.0"

from .config import AppConfig, DetectorConfig, FrameSourceConfig, VisualizerConfig
from .detector import Detector
from .frame_source import FrameSource, WebcamFrameSource
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
    "Tracker",
    "CentroidTracker",
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
