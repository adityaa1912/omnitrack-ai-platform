"""
FastAPI backend for YOLOv8 inference pipeline.

Exposes REST API for stream control and metrics.
Provides WebSocket for real-time frame streaming.
"""

import asyncio
import logging
import base64
import cv2
import numpy as np
from typing import Optional, List
from datetime import datetime

from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, Query
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


# Event history query bounds (read from the in-memory event store).
DEFAULT_EVENT_LIMIT = 100
MAX_EVENT_LIMIT = 1000

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


class EventResponse(BaseModel):
    """A single derived event record from the in-memory event store.

    Mirrors `Event.to_dict()` plus the `seq` stamped by the event buffer. `seq`
    is a monotonic, gap-free ordering key per stream and is preserved here so
    clients can de-duplicate and order events independently of `timestamp`.
    """
    seq: int
    stream_id: str
    event_type: str
    severity: str
    severity_rank: int
    frame_id: int
    timestamp: float
    track_id: Optional[int] = None
    class_name: Optional[str] = None
    message: str = ""
    metadata: dict = Field(default_factory=dict)


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


@app.get("/stream/{stream_id}/events", response_model=List[EventResponse])
async def get_events(
    stream_id: str,
    limit: int = Query(
        default=DEFAULT_EVENT_LIMIT,
        ge=1,
        le=MAX_EVENT_LIMIT,
        description="Maximum number of events to return, newest-first.",
    ),
):
    """Get recent derived events for a stream, newest-first.

    Reads from the in-memory event store only (no database). Returns an empty
    list for streams with no buffered history (unknown, or never produced an
    event), so this endpoint never 404s on a valid stream id. `seq` ordering
    keys are preserved on each record.
    """
    events = service.get_stream_events(stream_id, limit=limit)
    return [EventResponse(**event) for event in events]


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
# WebSocket Endpoint for Live Event Streaming
# ============================================================================

# Per-connection bound on buffered live events. This is independent of the event
# store's retention: it caps how many events may queue for a single slow client
# before the oldest is dropped, so one stalled consumer can neither grow server
# memory without bound nor back-pressure the inference thread.
EVENT_WS_QUEUE_MAXSIZE = 100


@app.websocket("/stream/{stream_id}/events/ws")
async def websocket_events(websocket: WebSocket, stream_id: str):
    """
    WebSocket endpoint for live derived events (pushed, never polled).

    A dedicated transport, fully independent of the frame WebSocket
    (`/stream/{stream_id}/ws`): it carries only small JSON event records, never
    frames or telemetry. The client subscribes to the stream's EventBuffer and
    each newly appended event is pushed as it is produced.

    Isolation & bounded memory:
      - The buffer invokes our subscriber callback on the *inference* thread.
        That callback does nothing but hand the record to this connection's
        event loop via `loop.call_soon_threadsafe`, so event derivation never
        blocks on a socket and the frame hot path is untouched.
      - Each client owns a bounded `asyncio.Queue` (EVENT_WS_QUEUE_MAXSIZE). A
        consumer that falls behind never grows memory without bound: the oldest
        queued event is evicted (recency wins for a live feed) and the client is
        told, once, via a coalesced `gap` notice carrying how many it missed.
      - The subscription is always torn down on disconnect (the `finally`), so a
        departed client leaves no callback registered on the buffer.
    """
    await websocket.accept()
    logger.info(f"Event WebSocket client connected to stream {stream_id}")

    buffer = service.event_store.get_or_create(stream_id)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=EVENT_WS_QUEUE_MAXSIZE)

    # Events dropped to make room since the last `gap` notice was delivered.
    # Mutated only on the event-loop thread (in `_enqueue`, scheduled via
    # call_soon_threadsafe, and in the send loop), so it needs no lock.
    dropped_since_gap = 0

    def _enqueue(record: dict) -> None:
        """Place one record on the queue. Runs on the event-loop thread.

        On overflow, evict the oldest queued event and remember the drop; the
        send loop coalesces all drops into a single `gap` notification. The
        newest event is always retained.
        """
        nonlocal dropped_since_gap
        try:
            queue.put_nowait(record)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()  # drop the oldest to make room
            except asyncio.QueueEmpty:
                pass
            dropped_since_gap += 1
            try:
                queue.put_nowait(record)
            except asyncio.QueueFull:  # pragma: no cover - defensive
                pass

    def _on_event(record: dict) -> None:
        """Buffer subscriber callback. Runs on the INFERENCE thread.

        Must be cheap and non-blocking: it only schedules `_enqueue` on this
        connection's loop. Guarded so a late append during shutdown (loop
        closing) can never raise back into the producer.
        """
        try:
            loop.call_soon_threadsafe(_enqueue, record)
        except RuntimeError:  # event loop is closed/closing
            pass

    token = buffer.subscribe(_on_event)

    async def _send_loop() -> None:
        """Drain the queue forever, blocking on `get()` — no polling."""
        nonlocal dropped_since_gap
        while True:
            record = await queue.get()
            if dropped_since_gap:
                missed = dropped_since_gap
                dropped_since_gap = 0
                await websocket.send_json({"type": "gap", "dropped": missed})
            await websocket.send_json({"type": "event", "event": record})

    async def _watch_disconnect() -> None:
        """Surface a client disconnect even when no events are flowing.

        This channel is push-only; we never act on client messages, but reading
        drains them and lets `receive()` report the disconnect promptly.
        """
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return

    send_task = asyncio.create_task(_send_loop())
    watch_task = asyncio.create_task(_watch_disconnect())
    try:
        # Whichever finishes first ends the session: a send failure (client
        # gone) or an observed disconnect.
        await asyncio.wait(
            {send_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        buffer.unsubscribe(token)  # stop producing into a dead connection
        for task in (send_task, watch_task):
            task.cancel()
        # Consume any pending result/exception so it isn't logged as
        # "Task exception was never retrieved".
        for task in (send_task, watch_task):
            try:
                await task
            except (WebSocketDisconnect, asyncio.CancelledError):
                pass
            except Exception:  # noqa: BLE001 - disconnect races are expected
                pass
        logger.info(f"Event WebSocket client disconnected from stream {stream_id}")


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
            "GET /stream/{stream_id}/events": "Get recent derived events (newest-first)",
            "GET /streams": "List all active streams",
            "WS /stream/{stream_id}/ws": "Real-time frame streaming",
            "WS /stream/{stream_id}/events/ws": "Real-time event streaming",
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
