"""Real-time YOLOv8 object detection pipeline."""

__version__ = "0.1.0"

from .config import AppConfig, DetectorConfig, FrameSourceConfig, VisualizerConfig
from .detector import Detector
from .frame_source import FrameSource, WebcamFrameSource
from .logger import get_logger, setup_logging
from .types import Detection, Frame, InferenceResult
from .visualizer import FpsCounter, Visualizer

__all__ = [
    "AppConfig",
    "DetectorConfig",
    "FrameSourceConfig",
    "VisualizerConfig",
    "Detector",
    "FrameSource",
    "WebcamFrameSource",
    "Detection",
    "Frame",
    "InferenceResult",
    "FpsCounter",
    "Visualizer",
    "get_logger",
    "setup_logging",
]
