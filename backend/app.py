"""
FastAPI backend for YOLOv8 inference pipeline.

Exposes REST API for stream control and metrics.
Provides WebSocket for real-time frame streaming.
"""

import logging
import base64
import cv2
import numpy as np
from typing import Optional, List
from datetime import datetime

from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import numpy as np

from .service import InferenceService, StreamConfig, StreamMetrics
from inference.types import Detection as InferenceDetection


logger = logging.getLogger(__name__)


def _to_native(value):
    """Convert NumPy scalar types to native Python types for JSON serialization."""
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


# Initialize service
service = InferenceService(db_path="inference_data.db")

# Create FastAPI app
app = FastAPI(
    title="YOLOv8 Inference API",
    description="Real-time object detection with multi-object tracking",
    version="0.3.0",
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Pydantic Models (Request/Response schemas)
# ============================================================================

class StartStreamRequest(BaseModel):
    """Request to start a new inference stream."""
    stream_id: str
    source: int | str  # 0 for webcam, file path, or RTSP URL
    width: int = 640
    height: int = 480
    fps: int = 30
    confidence_threshold: float = 0.5
    tracking_enabled: bool = True
    track_distance: float = 50.0
    max_age: int = 30


class DetectionResponse(BaseModel):
    """Single detection object."""
    x1: float
    y1: float
    x2: float
    y2: float
    class_id: int
    class_name: str
    confidence: float
    track_id: Optional[int] = None


class MetricsResponse(BaseModel):
    """Stream metrics."""
    stream_id: str
    fps: float
    total_frames: int
    total_detections: int
    inference_time_avg_ms: float
    is_active: bool
    error_message: Optional[str] = None


class StreamResponse(BaseModel):
    """Stream status."""
    stream_id: str
    source: str
    is_running: bool
    metrics: MetricsResponse


class HealthResponse(BaseModel):
    """Service health status."""
    status: str
    timestamp: datetime
    active_streams: int
    version: str = "0.3.0"


# ============================================================================
# REST API Endpoints
# ============================================================================

@app.get("/health", response_model=HealthResponse)
async def health():
    """Service health check endpoint."""
    return HealthResponse(
        status="healthy",
        timestamp=datetime.utcnow(),
        active_streams=len(service.streams),
    )


@app.post("/stream/start", response_model=MetricsResponse)
async def start_stream(request: StartStreamRequest):
    """Start a new inference stream."""
    try:
        config = StreamConfig(
            stream_id=request.stream_id,
            source=request.source,
            width=request.width,
            height=request.height,
            fps=request.fps,
            confidence_threshold=request.confidence_threshold,
            tracking_enabled=request.tracking_enabled,
            track_distance=request.track_distance,
            max_age=request.max_age,
        )
        metrics = service.start_stream(config)

        return MetricsResponse(
            stream_id=metrics.stream_id,
            fps=metrics.fps,
            total_frames=metrics.total_frames,
            total_detections=metrics.total_detections,
            inference_time_avg_ms=metrics.inference_time_avg_ms,
            is_active=metrics.is_active,
            error_message=metrics.error_message,
        )

    except Exception as e:
        logger.error(f"Failed to start stream: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/stream/stop")
async def stop_stream(stream_id: str):
    """Stop an inference stream."""
    try:
        service.stop_stream(stream_id)
        return {"status": "stopped", "stream_id": stream_id}

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to stop stream: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stream/{stream_id}/metrics", response_model=MetricsResponse)
async def get_metrics(stream_id: str):
    """Get metrics for a specific stream."""
    try:
        metrics = service.get_stream_metrics(stream_id)

        return MetricsResponse(
            stream_id=metrics.stream_id,
            fps=metrics.fps,
            total_frames=metrics.total_frames,
            total_detections=metrics.total_detections,
            inference_time_avg_ms=metrics.inference_time_avg_ms,
            is_active=metrics.is_active,
            error_message=metrics.error_message,
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/stream/{stream_id}/detections", response_model=List[DetectionResponse])
async def get_detections(stream_id: str):
    """Get latest detections from a stream."""
    try:
        tracked_objects = service.get_stream_detections(stream_id)

        detections = []
        for obj in tracked_objects:
            # Extract detection from TrackedObject
            detection = obj.current_detection
            if detection is not None:
                detections.append(
                    DetectionResponse(
                        x1=detection.x1,
                        y1=detection.y1,
                        x2=detection.x2,
                        y2=detection.y2,
                        class_id=detection.class_id,
                        class_name=detection.class_name,
                        confidence=detection.confidence,
                        track_id=obj.track_id,
                    )
                )
        return detections

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/streams", response_model=List[StreamResponse])
async def list_streams():
    """List all active streams."""
    streams = service.list_streams()

    return [
        StreamResponse(
            stream_id=s["stream_id"],
            source=s["source"],
            is_running=s["is_running"],
            metrics=MetricsResponse(
                stream_id=s["metrics"].stream_id,
                fps=s["metrics"].fps,
                total_frames=s["metrics"].total_frames,
                total_detections=s["metrics"].total_detections,
                inference_time_avg_ms=s["metrics"].inference_time_avg_ms,
                is_active=s["metrics"].is_active,
                error_message=s["metrics"].error_message,
            ),
        )
        for s in streams
    ]


# ============================================================================
# WebSocket Endpoint for Real-time Frame Streaming
# ============================================================================

@app.websocket("/stream/{stream_id}/ws")
async def websocket_stream(websocket: WebSocket, stream_id: str):
    """
    WebSocket endpoint for real-time frame streaming.

    Sends JPEG-encoded frames as binary data with detection overlays.
    Client can subscribe to a stream and receive frames in real-time.
    """
    await websocket.accept()

    logger.info(f"WebSocket client connected to stream {stream_id}")

    try:
        while True:
            try:
                # Get next frame from stream
                frame_data = service.get_stream_frame(stream_id, timeout=1.0)

                if frame_data is None:
                    # No frame available, send keep-alive
                    await websocket.send_json({"type": "keep_alive"})
                    continue

                # Encode frame as JPEG
                frame = frame_data["frame"]
                _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                frame_base64 = base64.b64encode(buffer).decode("utf-8")

                # Send frame with detections (coerce NumPy scalars -> native)
                await websocket.send_json({
                    "type": "frame",
                    "stream_id": stream_id,
                    "timestamp": _to_native(frame_data["timestamp"]),
                    "frame": frame_base64,
                    "detections": [
                        {
                            "x1": _to_native(d.x1),
                            "y1": _to_native(d.y1),
                            "x2": _to_native(d.x2),
                            "y2": _to_native(d.y2),
                            "class_id": _to_native(d.class_id),
                            "class_name": d.class_name,
                            "confidence": f"{float(d.confidence):.2f}",
                            "track_id": (
                                None
                                if getattr(d, "track_id", None) is None
                                else int(getattr(d, "track_id"))
                            ),
                        }
                        for d in frame_data["detections"]
                    ],
                })

            except WebSocketDisconnect:
                logger.info(f"WebSocket client disconnected from stream {stream_id}")
                break

    except Exception as e:
        logger.error(f"WebSocket error for stream {stream_id}: {e}")
        await websocket.close(code=1011, reason=str(e))


# ============================================================================
# Static Info Endpoints
# ============================================================================

@app.get("/")
async def root():
    """API root - returns service info."""
    return {
        "name": "YOLOv8 Inference API",
        "version": "0.3.0",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "POST /stream/start": "Start a new inference stream",
            "POST /stream/stop": "Stop an inference stream",
            "GET /stream/{stream_id}/metrics": "Get stream metrics",
            "GET /stream/{stream_id}/detections": "Get latest detections",
            "GET /streams": "List all active streams",
            "WS /stream/{stream_id}/ws": "Real-time frame streaming",
            "GET /health": "Service health check",
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
