"""
FastAPI backend for YOLOv8 inference pipeline.

Provides REST API and WebSocket support for:
- Stream control (start/stop)
- Real-time metrics
- Detection results
- Frame streaming
"""

from .service import InferenceService, InferenceStream, StreamConfig, StreamMetrics
from .models import Detection, Metric, StreamSession, get_database_session
from .app import app

__version__ = "0.3.0"

__all__ = [
    "app",
    "InferenceService",
    "InferenceStream",
    "StreamConfig",
    "StreamMetrics",
    "Detection",
    "Metric",
    "StreamSession",
    "get_database_session",
]
