"""
FastAPI backend for YOLOv8 inference pipeline.

Provides REST API and WebSocket support for:
- Stream control (start/stop)
- Real-time metrics
- Detection results
- Frame streaming
"""

from .service import InferenceService, InferenceStream, StreamConfig, StreamMetrics
from .models import Detection, Metric, StreamSession, create_session_factory

# The FastAPI app is intentionally NOT imported here, so importing `backend`
# (e.g. `backend.models` from Alembic's env.py) never constructs the service or
# creates database tables as an import side effect. Get the app explicitly via
# `from backend.main import app` (as run.py and uvicorn do).

__version__ = "0.3.0"

__all__ = [
    "InferenceService",
    "InferenceStream",
    "StreamConfig",
    "StreamMetrics",
    "Detection",
    "Metric",
    "StreamSession",
    "create_session_factory",
]
