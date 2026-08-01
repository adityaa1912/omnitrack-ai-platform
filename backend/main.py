"""
FastAPI backend for YOLOv8 inference pipeline.

Exposes REST API for stream control and metrics.
Provides WebSocket for real-time frame streaming.
"""

import asyncio
import hmac
import logging
import os
import base64
import cv2
import numpy as np
from contextlib import asynccontextmanager
from typing import Optional, List, Tuple
from datetime import datetime

from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketState
from pydantic import BaseModel, Field
import numpy as np

from .service import InferenceService, StreamConfig, StreamMetrics
from inference.types import Detection as InferenceDetection
from inference.events.regions import Zone, CrossingLine


logger = logging.getLogger(__name__)


def _to_native(value):
    """Convert NumPy scalar types to native Python types for JSON serialization."""
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def _cors_config() -> tuple[list[str], bool]:
    """Resolve CORS allowed origins from the environment.

    ``OMNITRACK_CORS_ORIGINS`` is a comma-separated list of allowed origins;
    unset or empty defaults to ``*`` (permissive, suitable for local dev, e.g.
    ``OMNITRACK_CORS_ORIGINS=https://app.example.com,https://admin.example.com``).

    Per the CORS spec a wildcard origin and credentials are mutually exclusive
    (browsers reject ``Access-Control-Allow-Origin: *`` with credentials), so
    credentials are enabled only when explicit origins are configured. If ``*``
    appears anywhere in the list it wins (wildcard, credentials off).
    """
    raw = os.environ.get("OMNITRACK_CORS_ORIGINS", "*")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if not origins or "*" in origins:
        return ["*"], False
    return origins, True


# ---- API-key access control (opt-in) --------------------------------------
# Set OMNITRACK_API_KEY to require callers to present the same value via the
# `X-API-Key` header (REST) or `?api_key=` query param (WebSocket). Unset leaves
# the API open (unchanged local-dev behavior).
# NOTE: a browser SPA that embeds the key exposes it to end users, so this is a
# shared-secret gate for locked-down deployments and non-browser clients — not a
# substitute for real user authentication.
API_KEY = os.environ.get("OMNITRACK_API_KEY", "").strip() or None

# Paths that never require a key (health checks, docs, service root).
_AUTH_EXEMPT_PATHS = frozenset({"/health", "/", "/docs", "/redoc", "/openapi.json"})


def _check_api_key(provided: Optional[str]) -> bool:
    """Return True if the request is authorized.

    Auth is opt-in: when ``API_KEY`` is unset every request passes. When set,
    the caller must present the same value, compared in constant time to avoid
    timing leaks.
    """
    if API_KEY is None:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided, API_KEY)


def _build_geometry(zone_specs, line_specs):
    """Translate region specs into inference ``Zone``/``CrossingLine`` objects.

    Raises ``ValueError`` (from the inference constructors' own validation) on
    invalid geometry — callers map that to HTTP 422. Shared by the start and
    live-reconfigure endpoints so the translation lives in exactly one place.
    """
    zones = tuple(Zone(name=s.name, polygon=s.polygon) for s in zone_specs)
    lines = tuple(
        CrossingLine(
            name=s.name,
            start=s.start,
            end=s.end,
            positive_label=s.positive_label,
            negative_label=s.negative_label,
        )
        for s in line_specs
    )
    return zones, lines


# Event history query bounds (read from the in-memory event store).
DEFAULT_EVENT_LIMIT = 100
MAX_EVENT_LIMIT = 1000

# Initialize service
service = InferenceService(db_path="inference_data.db")


class StreamWebSocketManager:
    """Coordinate live WebSocket handlers with a stream's lifecycle.

    The manager deliberately does not call ``WebSocket.close()`` itself.  A
    handler is the sole owner of its ASGI WebSocket and closes it after its
    stop event is set.  This prevents concurrent ASGI close frames, which is
    what left the underlying ``websockets`` protocol in an invalid state.
    """

    def __init__(self) -> None:
        self._stop_events: dict[str, set[asyncio.Event]] = {}

    def register(self, stream_id: str) -> asyncio.Event:
        stop_event = asyncio.Event()
        self._stop_events.setdefault(stream_id, set()).add(stop_event)
        return stop_event

    def unregister(self, stream_id: str, stop_event: asyncio.Event) -> None:
        connections = self._stop_events.get(stream_id)
        if connections is None:
            return
        connections.discard(stop_event)
        if not connections:
            self._stop_events.pop(stream_id, None)

    def close_stream(self, stream_id: str) -> None:
        for stop_event in tuple(self._stop_events.get(stream_id, ())):
            stop_event.set()

    def close_all(self) -> None:
        for stream_id in tuple(self._stop_events):
            self.close_stream(stream_id)


stream_websockets = StreamWebSocketManager()


async def _close_stopped_stream_socket(websocket: WebSocket) -> None:
    """Send the one, normal close frame owned by a stopped-stream handler."""
    if websocket.application_state is WebSocketState.CONNECTED:
        await websocket.close(code=1000, reason="stream stopped")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Application lifespan.

    Startup is a no-op — the inference service is constructed at import. On
    shutdown (SIGINT/SIGTERM/uvicorn reload) we stop every running inference
    thread, release frame sources, flush pending DB writes, and dispose the
    database engine so nothing leaks and stream sessions are closed out.
    `service.shutdown()` blocks on bounded per-stream joins, so it runs in a
    worker thread to avoid stalling the event loop during teardown.
    """
    yield
    logger.info("Application shutdown: stopping streams and disposing resources")
    stream_websockets.close_all()
    await asyncio.to_thread(service.shutdown)


# Create FastAPI app
app = FastAPI(
    title="YOLOv8 Inference API",
    description="Real-time object detection with multi-object tracking",
    version="0.3.0",
    lifespan=lifespan,
)

# Enable CORS for frontend integration. Origins are configurable via
# OMNITRACK_CORS_ORIGINS (comma-separated); defaults to '*' for local dev.
_cors_origins, _cors_allow_credentials = _cors_config()
app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def _api_key_middleware(request: Request, call_next):
    """Enforce the API key on HTTP routes when auth is enabled.

    Exempts CORS preflight (OPTIONS carries no custom headers) and the public
    paths. WebSocket handshakes use the ``websocket`` ASGI scope and bypass HTTP
    middleware, so they are authorized inside their handlers instead.
    """
    if (
        API_KEY is not None
        and request.method != "OPTIONS"
        and request.url.path not in _AUTH_EXEMPT_PATHS
    ):
        if not _check_api_key(request.headers.get("x-api-key")):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )
    return await call_next(request)


# ============================================================================
# Pydantic Models (Request/Response schemas)
# ============================================================================

class ZoneSpec(BaseModel):
    """A named polygonal zone (>= 3 vertices, pixel coordinates).

    Drives OBJECT_ENTERED / OBJECT_EXITED / DWELL_TIME events. The vertex-count
    and coordinate validation is enforced by the inference `Zone` type, so this
    schema only pins the wire shape.
    """
    name: str
    polygon: List[Tuple[float, float]]


class LineSpec(BaseModel):
    """A named directed tripwire for CROSSING_DIRECTION events.

    `positive_label` / `negative_label` name the two crossing directions; the
    start->end order fixes which physical direction each label means.
    """
    name: str
    start: Tuple[float, float]
    end: Tuple[float, float]
    positive_label: str = "positive"
    negative_label: str = "negative"


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

    # Optional scene geometry for the event engine's zone/line detectors. Empty
    # (the default) leaves those detectors inert, so an unconfigured stream
    # behaves exactly as before. Requires tracking_enabled=True to have effect.
    zones: List[ZoneSpec] = Field(default_factory=list)
    lines: List[LineSpec] = Field(default_factory=list)
    dwell_seconds: float = Field(default=5.0, gt=0)


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


class RegionsResponse(BaseModel):
    """The scene-region geometry configured on a stream's event engine.

    Reuses the request specs as the response shape (the contract is symmetric:
    what you POST at start is what you GET back). Regions are fixed at stream
    start, so this reflects exactly what the engine is using.
    """
    stream_id: str
    zones: List[ZoneSpec]
    lines: List[LineSpec]
    dwell_seconds: float
    # Source frame dimensions the region coordinates are expressed in, so a
    # client can render regions in the correct pixel space (e.g. an overlay).
    width: int
    height: int


class RegionsUpdateRequest(BaseModel):
    """Body for PUT /stream/{id}/regions — replaces a running stream's geometry.

    Same shape as the start-request geometry fields. Coordinates are in the
    stream's source-frame pixel space (unchanged by this call).
    """
    zones: List[ZoneSpec] = Field(default_factory=list)
    lines: List[LineSpec] = Field(default_factory=list)
    dwell_seconds: float = Field(default=5.0, gt=0)


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
    # Translate scene-region specs into inference geometry up front, so invalid
    # geometry is rejected (422) before any stream resource is opened. This
    # reuses the inference layer's own validation as the single source of truth.
    try:
        zones, lines = _build_geometry(request.zones, request.lines)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

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
            zones=zones,
            lines=lines,
            dwell_seconds=request.dwell_seconds,
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
        # Wake every handler first.  Each handler owns and sends its own close
        # frame, so this cannot race a send/receive task with a second close.
        stream_websockets.close_stream(stream_id)
        # ``stop`` may join the inference thread; never block the ASGI loop
        # while WebSocket handlers are processing their lifecycle signal.
        await asyncio.to_thread(service.stop_stream, stream_id)
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


@app.get("/stream/{stream_id}/regions", response_model=RegionsResponse)
async def get_regions(stream_id: str):
    """Return the scene-region geometry configured on a stream (zones/lines).

    404 if the stream is not active. Regions are fixed at stream start, so this
    reflects exactly what the event engine is using.
    """
    regions = service.get_stream_regions(stream_id)
    if regions is None:
        raise HTTPException(status_code=404, detail=f"Stream {stream_id} not found")
    return regions


@app.put("/stream/{stream_id}/regions", response_model=RegionsResponse)
async def update_regions(stream_id: str, request: RegionsUpdateRequest):
    """Replace a running stream's scene regions live — no restart.

    Rebuilds the event engine's geometry detectors in place (existing tracks are
    preserved, so this never re-fires appearance events). Returns the updated
    regions. Status codes: 422 invalid geometry; 404 stream not active; 409 the
    stream is not running or has tracking disabled.
    """
    try:
        zones, lines = _build_geometry(request.zones, request.lines)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if service.get_stream_regions(stream_id) is None:
        raise HTTPException(status_code=404, detail=f"Stream {stream_id} not found")

    try:
        return service.reconfigure_stream(
            stream_id, zones, lines, request.dwell_seconds
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


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
    if not _check_api_key(websocket.query_params.get("api_key")):
        await websocket.close(code=1008)  # policy violation
        return

    await websocket.accept()
    logger.info(f"WebSocket client connected to stream {stream_id}")
    stop_event = stream_websockets.register(stream_id)

    async def _watch_disconnect() -> None:
        """Wait for a client close while frame delivery is otherwise idle."""
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

    frame_task: Optional[asyncio.Task] = None
    disconnect_task: Optional[asyncio.Task] = None
    stop_task: Optional[asyncio.Task] = None
    try:
        # Register before the existence check.  There is no await between them,
        # so a concurrent stop cannot delete the stream without waking us.
        if not service.has_stream(stream_id):
            await _close_stopped_stream_socket(websocket)
            return

        disconnect_task = asyncio.create_task(_watch_disconnect())
        stop_task = asyncio.create_task(stop_event.wait())
        while True:
            # Queue.get is synchronous.  Running this bounded wait in a worker
            # keeps the ASGI loop responsive to a disconnect or stream stop.
            frame_task = asyncio.create_task(
                asyncio.to_thread(service.get_stream_frame, stream_id, 1.0)
            )
            done, _ = await asyncio.wait(
                {frame_task, disconnect_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if stop_task in done:
                await _close_stopped_stream_socket(websocket)
                return
            if disconnect_task in done:
                logger.info(f"WebSocket client disconnected from stream {stream_id}")
                return

            try:
                frame_data = frame_task.result()
            except ValueError:
                # A naturally-finished/reaped stream is terminal, not a
                # WebSocket server error and must not provoke reconnects.
                await _close_stopped_stream_socket(websocket)
                return
            finally:
                frame_task = None

            if frame_data is None:
                await websocket.send_json({"type": "keep_alive"})
                continue

            frame = frame_data["frame"]
            _, buffer = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80]
            )
            frame_base64 = base64.b64encode(buffer).decode("utf-8")
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
    finally:
        for task in (frame_task, disconnect_task, stop_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (frame_task, disconnect_task, stop_task) if task is not None),
            return_exceptions=True,
        )
        stream_websockets.unregister(stream_id, stop_event)


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
    if not _check_api_key(websocket.query_params.get("api_key")):
        await websocket.close(code=1008)  # policy violation
        return

    await websocket.accept()
    logger.info(f"Event WebSocket client connected to stream {stream_id}")
    stop_event = stream_websockets.register(stream_id)

    # Do not create an EventBuffer for an unknown/deleted stream.  Register
    # first so a stop racing this accepted connection cannot be missed.
    if not service.has_stream(stream_id):
        try:
            await _close_stopped_stream_socket(websocket)
        finally:
            stream_websockets.unregister(stream_id, stop_event)
        return

    buffer = service.event_store.get(stream_id)
    if buffer is None:  # defensive: start_stream always creates this buffer
        try:
            await _close_stopped_stream_socket(websocket)
        finally:
            stream_websockets.unregister(stream_id, stop_event)
        return
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
    stop_task = asyncio.create_task(stop_event.wait())
    try:
        # Whichever finishes first ends the session: a send failure (client
        # gone), an observed disconnect, or a stream lifecycle stop.
        done, _ = await asyncio.wait(
            {send_task, watch_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done:
            await _close_stopped_stream_socket(websocket)
        elif watch_task in done:
            logger.info(f"Event WebSocket client disconnected from stream {stream_id}")
        else:
            # Surface only the send task's expected WebSocketDisconnect to the
            # outer handler; unexpected faults are not masked as disconnects.
            await send_task
    except WebSocketDisconnect:
        logger.info(f"Event WebSocket client disconnected from stream {stream_id}")
    finally:
        buffer.unsubscribe(token)  # stop producing into a dead connection
        for task in (send_task, watch_task, stop_task):
            task.cancel()
        # Retrieve every outcome so cancelled/send tasks never leak warnings.
        await asyncio.gather(send_task, watch_task, stop_task, return_exceptions=True)
        stream_websockets.unregister(stream_id, stop_event)
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
            "GET /stream/{stream_id}/regions": "Get configured scene regions (zones/lines)",
            "PUT /stream/{stream_id}/regions": "Update scene regions live (running stream)",
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
