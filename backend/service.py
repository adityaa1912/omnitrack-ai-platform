"""
Inference service layer - manages inference pipelines and streams.

Provides a clean interface to control inference pipelines:
- Start/stop streams
- Track active detections
- Maintain metrics
- Persist data to database

Reuses existing inference modules (Detector, FrameSource, Tracker, Visualizer).
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from queue import Queue
import numpy as np

from inference import (
    AppConfig,
    get_frame_source,
    Detector,
    CentroidTracker,
    Visualizer,
)
from inference.config import (
    DetectorConfig,
    FrameSourceConfig,
    VisualizerConfig,
)
from inference.tracking_config import TrackingConfig
from inference.types import Detection, Frame
from inference.events import EventEngine, EventEngineConfig
from inference.events.regions import CrossingLine, Zone
from .models import Detection as DetectionRecord, StreamSession, create_session_factory
from .event_store import EventStore


logger = logging.getLogger(__name__)


def _to_python_type(value):
    """Convert NumPy types to native Python types."""
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    return value


@dataclass
class StreamConfig:
    """Configuration for a single inference stream."""
    stream_id: str
    source: int | str  # 0 for webcam, path/URL for file/RTSP
    width: int = 640
    height: int = 480
    fps: int = 30
    confidence_threshold: float = 0.5
    tracking_enabled: bool = True
    track_distance: float = 50.0
    max_age: int = 30

    # Scene geometry consumed by the event engine's zone/line detectors. Empty
    # by default => those detectors stay inert (behaviorally unchanged). Built
    # by the API layer from validated request specs and fixed for the stream's
    # lifetime, so the inference thread reads an immutable config (no locks).
    zones: Tuple[Zone, ...] = ()
    lines: Tuple[CrossingLine, ...] = ()
    dwell_seconds: float = 5.0


@dataclass
class StreamMetrics:
    """Current metrics for an active stream."""
    stream_id: str
    fps: float = 0.0
    total_frames: int = 0
    total_detections: int = 0
    inference_time_avg_ms: float = 0.0
    is_active: bool = True
    error_message: Optional[str] = None


class InferenceStream:
    """Manages a single inference stream (frame source → detector → tracker → visualizer)."""

    def __init__(self, config: StreamConfig, session_factory, event_buffer=None) -> None:
        self.config = config
        # Thread-safe session registry (scoped_session). Each thread that writes
        # obtains its OWN session via `self._session_factory()` and releases it
        # with `.remove()` when done; a Session is never shared across threads.
        self._session_factory = session_factory

        # Components
        self.frame_source = None
        self.detector = None
        self.tracker = None
        self.visualizer = None
        self.event_engine: Optional[EventEngine] = None

        # Bounded in-memory event sink (ring buffer owned by the service).
        self.event_buffer = event_buffer

        # State
        self.is_running = False
        self.thread: Optional[threading.Thread] = None

        # Metrics
        self.metrics = StreamMetrics(stream_id=config.stream_id)
        self.inference_times = []
        self.latest_detections: List[Detection] = []
        self.latest_frame: Optional[Frame] = None

        # Output queue for frames/detections
        self.output_queue: Queue = Queue(maxsize=30)

    def start(self) -> None:
        """Start the inference stream in a background thread."""
        if self.is_running:
            logger.warning(f"Stream {self.config.stream_id} already running")
            return

        try:
            self._initialize_components()
            self.is_running = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

            # Record the session row on THIS (request) thread's own session.
            db = self._session_factory()
            try:
                db.add(
                    StreamSession(
                        stream_id=self.config.stream_id,
                        source=str(self.config.source),
                        width=self.config.width,
                        height=self.config.height,
                        tracking_enabled=self.config.tracking_enabled,
                    )
                )
                db.commit()
            except Exception as db_err:
                db.rollback()
                logger.error(f"Database error while recording stream session: {db_err}")
                raise
            finally:
                self._session_factory.remove()

            logger.info(f"Stream {self.config.stream_id} started")

        except Exception as e:
            logger.error(f"Failed to start stream {self.config.stream_id}: {e}")
            self.metrics.is_active = False
            self.metrics.error_message = str(e)
            raise

    def stop(self) -> None:
        """Stop the inference stream.

        Signals the loop to exit and waits for the thread to drain. The session
        row is closed and buffered detections are flushed by the loop's own
        `finally` (see `_run` / `_finalize_session`) — for EVERY termination
        cause, not just an explicit stop — so this method performs no DB writes
        and holds no session. That keeps all of a stream's DB access on its own
        thread and makes reaping a naturally-finished stream trivial.
        """
        if not self.is_running:
            logger.warning(f"Stream {self.config.stream_id} not running")
            return

        self.is_running = False
        if self.thread is not None:
            self.thread.join(timeout=5.0)
        self._cleanup()
        logger.info(f"Stream {self.config.stream_id} stopped")

    def _initialize_components(self) -> None:
        """Initialize detector, tracker, visualizer, frame source."""
        detector_config = DetectorConfig(
            confidence_threshold=self.config.confidence_threshold,
        )
        self.detector = Detector(detector_config)

        frame_source_config = FrameSourceConfig(
            source=self.config.source,
            width=self.config.width,
            height=self.config.height,
            fps=self.config.fps,
        )
        self.frame_source = get_frame_source(frame_source_config)
        self.frame_source.open()

        if self.config.tracking_enabled:
            tracking_config = TrackingConfig(
                enabled=True,
                centroid_distance_threshold=self.config.track_distance,
                max_age=self.config.max_age,
            )
            self.tracker = CentroidTracker(tracking_config)

            # Events derive from tracker output, so the engine exists only when
            # tracking is enabled. Geometry detectors are active only when the
            # config carries zones/lines; otherwise they stay inert.
            self.event_engine = EventEngine(
                stream_id=self.config.stream_id,
                config=EventEngineConfig(
                    zones=self.config.zones,
                    lines=self.config.lines,
                    dwell_seconds=self.config.dwell_seconds,
                ),
            )

        visualizer_config = VisualizerConfig(
            show_fps=True,
            show_confidence=True,
            show_trajectories=True,
        )
        self.visualizer = Visualizer(visualizer_config)

    def _cleanup(self) -> None:
        """Clean up resources."""
        if self.frame_source is not None:
            self.frame_source.close()
        if self.detector is not None:
            self.detector.model = None

    def _run(self) -> None:
        """Main inference loop (runs in background thread)."""
        # This thread's own database session (see `create_session_factory`);
        # confined to this thread and released in the `finally` below.
        db = self._session_factory()
        try:
            logger.info(f"Inference loop started for {self.config.stream_id}")

            for frame in self.frame_source.read():
                if not self.is_running:
                    break

                try:
                    # Run inference
                    result = self.detector.predict(frame)
                    self.latest_frame = frame
                    self.metrics.total_frames += 1

                    # Run tracking if enabled
                    tracking_result = None
                    if self.tracker is not None:
                        tracking_result = self.tracker.update(
                            result.detections,
                            frame.frame_id,
                            frame.timestamp,
                        )
                        self.latest_detections = tracking_result.tracked_objects

                        # Render with tracking
                        output_frame = self.visualizer.render_tracked(
                            frame.data,
                            tracking_result,
                            fps=self.metrics.fps,
                        )
                    else:
                        self.latest_detections = result.detections
                        output_frame = self.visualizer.render(
                            frame.data,
                            result,
                            fps=self.metrics.fps,
                        )

                    # Update metrics
                    self._update_metrics(result.inference_time_ms, len(result.detections))

                    # Store detections in database
                    self._store_detections(db, result.detections, frame)

                    # Add to output queue (non-blocking)
                    try:
                        self.output_queue.put_nowait({
                            "frame": output_frame,
                            "detections": result.detections,
                            "timestamp": frame.timestamp,
                        })
                    except:
                        pass  # Queue full, drop frame

                    # Derive events AFTER the frame has been queued, so event
                    # generation sits off the frame-delivery path and can never
                    # delay or block it. Guarded internally so it can never
                    # break the inference loop.
                    if tracking_result is not None:
                        self._process_events(tracking_result)

                except Exception as e:
                    logger.error(f"Error in inference loop: {e}", exc_info=True)
                    self.metrics.is_active = False
                    self.metrics.error_message = str(e)
                    break

        finally:
            # Finalize on THIS thread's session, for every termination cause
            # (EOF, error, or an explicit stop): flush tail detections AND close
            # out the session row. Then release the session, mark the loop ended
            # (truthful status for the reaper), and free resources.
            self._finalize_session(db)
            self._session_factory.remove()
            self.is_running = False
            self._cleanup()
            logger.info(f"Inference loop finished for {self.config.stream_id}")

    def _finalize_session(self, db) -> None:
        """Close out the StreamSession row and flush any pending detections.

        Runs once per stream lifetime, on the inference thread's own session
        (from `_run`'s finally). Idempotent via the `ended_at` guard: only the
        first finalize stamps the end time and totals, so this never
        double-writes even if called again. The commit also flushes detections
        added since the last periodic commit.
        """
        try:
            record = (
                db.query(StreamSession)
                .filter_by(stream_id=self.config.stream_id)
                .first()
            )
            if record is not None and record.ended_at is None:
                record.is_active = False
                record.ended_at = datetime.utcnow()
                record.total_frames = self.metrics.total_frames
                record.total_detections = self.metrics.total_detections
            db.commit()
        except Exception as db_err:
            db.rollback()
            logger.error(
                f"Database error while finalizing stream session "
                f"{self.config.stream_id}: {db_err}"
            )

    def _process_events(self, tracking_result) -> None:
        """
        Derive events from one tracking result and append them to the event
        buffer.

        Called after the output frame has already been queued, so it is off the
        frame-delivery path. Wrapped in a broad guard: event derivation must
        never raise into — and so never stall or kill — the inference loop.
        Memory stays bounded (the engine prunes per-frame state to live tracks;
        the buffer is a fixed-capacity ring).
        """
        if self.event_engine is None or self.event_buffer is None:
            return
        try:
            events = self.event_engine.process(tracking_result)
            if events:
                self.event_buffer.extend(event.to_dict() for event in events)
        except Exception as exc:
            logger.error(
                f"Event processing error for stream {self.config.stream_id}: {exc}",
                exc_info=True,
            )

    def _update_metrics(self, inference_time_ms: float, num_detections: int) -> None:
        """Update running metrics."""
        self.inference_times.append(inference_time_ms)
        if len(self.inference_times) > 100:
            self.inference_times.pop(0)

        self.metrics.inference_time_avg_ms = sum(self.inference_times) / len(self.inference_times)
        self.metrics.fps = 1000.0 / max(self.metrics.inference_time_avg_ms, 1.0)
        self.metrics.total_detections += num_detections

    def _store_detections(self, db, detections: List[Detection], frame: Frame) -> None:
        """Persist detections using the calling thread's session `db`."""
        for det in detections:
            record = DetectionRecord(
                frame_id=frame.frame_id,
                timestamp=datetime.utcnow(),
                x1=_to_python_type(det.x1),
                y1=_to_python_type(det.y1),
                x2=_to_python_type(det.x2),
                y2=_to_python_type(det.y2),
                class_id=_to_python_type(det.class_id),
                class_name=det.class_name,
                confidence=_to_python_type(det.confidence),
                track_id=_to_python_type(getattr(det, 'track_id', None)),
                stream_id=self.config.stream_id,
                inference_time_ms=0.0,
            )
            db.add(record)

        # Batch commit every 100 frames to bound the unit-of-work size.
        if frame.frame_id % 100 == 0:
            try:
                db.commit()
            except Exception as db_err:
                db.rollback()
                logger.error(f"Database error while storing detections: {db_err}")

    def get_metrics(self) -> StreamMetrics:
        """Get current stream metrics."""
        return self.metrics

    def get_latest_detections(self) -> List[Detection]:
        """Get detections from latest frame."""
        return self.latest_detections

    def get_output_frame(self, timeout: float = 1.0) -> Optional[dict]:
        """Get next output frame (non-blocking or with timeout)."""
        try:
            return self.output_queue.get(timeout=timeout)
        except:
            return None

    def reconfigure(
        self,
        zones: Tuple[Zone, ...],
        lines: Tuple[CrossingLine, ...],
        dwell_seconds: float,
    ) -> None:
        """Apply new scene geometry to the running event engine, live.

        Delegates to ``EventEngine.reconfigure`` (which swaps the geometry
        detectors atomically w.r.t. the inference thread) and updates this
        stream's config so ``get_stream_regions`` stays accurate. Raises if the
        stream has no event engine (tracking disabled). Only geometry fields are
        touched; ``stream_id`` and the rest of the config are untouched, and the
        inference loop never reads these fields live, so this is race-free.
        """
        if self.event_engine is None:
            raise ValueError("Stream has no event engine (tracking is disabled)")
        self.event_engine.reconfigure(
            EventEngineConfig(zones=zones, lines=lines, dwell_seconds=dwell_seconds)
        )
        self.config.zones = zones
        self.config.lines = lines
        self.config.dwell_seconds = dwell_seconds


class InferenceService:
    """Service managing multiple inference streams."""

    def __init__(self, db_path: str = "inference_data.db", event_capacity: int = 1000) -> None:
        # Thread-safe DB access: one engine + a scoped_session registry shared by
        # every stream thread, each of which uses its own thread-local session.
        self.engine, self.Session = create_session_factory(db_path)
        self.streams: Dict[str, InferenceStream] = {}
        # Stop joins run in a worker so the ASGI loop can close WebSockets.
        # Guard the registry against a concurrent ``reap_finished`` removing a
        # stream in the narrow interval after ``stop`` flips is_running.
        self._streams_lock = threading.RLock()
        self._stopping: set[str] = set()
        # Bounded in-memory event history per stream (persists across stop).
        self.event_store = EventStore(capacity=event_capacity)
        self._is_shutdown = False

    def start_stream(self, config: StreamConfig) -> StreamMetrics:
        """Start a new inference stream."""
        # Drop any finished streams first, so a completed run does not block
        # reusing its stream_id.
        self.reap_finished()
        with self._streams_lock:
            if config.stream_id in self.streams:
                raise ValueError(f"Stream {config.stream_id} already exists")

        stream = InferenceStream(
            config,
            self.Session,
            event_buffer=self.event_store.get_or_create(config.stream_id),
        )
        stream.start()
        with self._streams_lock:
            self.streams[config.stream_id] = stream

        return stream.get_metrics()

    def stop_stream(self, stream_id: str) -> None:
        """Stop an inference stream."""
        with self._streams_lock:
            stream = self.streams.get(stream_id)
            if stream is None:
                raise ValueError(f"Stream {stream_id} not found")
            self._stopping.add(stream_id)

        try:
            stream.stop()
        finally:
            with self._streams_lock:
                # Do not remove a newer stream that reused the id after this
                # stream was stopped.
                if self.streams.get(stream_id) is stream:
                    self.streams.pop(stream_id, None)
                self._stopping.discard(stream_id)

    def has_stream(self, stream_id: str) -> bool:
        """Return whether ``stream_id`` is still registered as live."""
        with self._streams_lock:
            return stream_id in self.streams

    def shutdown(self) -> None:
        """
        Gracefully stop all streams and dispose the database engine.

        Idempotent; intended to be called once from the app's lifespan
        shutdown. Each stream is stopped cooperatively (flag flip + bounded
        join + resource cleanup + final DB flush); one stream failing to stop
        never blocks the others. Finally this thread's session is released and
        every pooled connection is closed.
        """
        if self._is_shutdown:
            return
        self._is_shutdown = True

        for stream_id in list(self.streams.keys()):
            try:
                self.stop_stream(stream_id)
            except Exception as exc:  # noqa: BLE001 - shutdown must be resilient
                logger.error(
                    f"Error stopping stream {stream_id} during shutdown: {exc}"
                )

        # Release the shutdown thread's session, then close all connections.
        self.Session.remove()
        self.engine.dispose()
        logger.info("Inference service shut down; database engine disposed")

    def get_stream_metrics(self, stream_id: str) -> StreamMetrics:
        """Get metrics for a specific stream."""
        if stream_id not in self.streams:
            raise ValueError(f"Stream {stream_id} not found")

        return self.streams[stream_id].get_metrics()

    def list_streams(self) -> List[dict]:
        """List all active streams (finished ones are reaped first)."""
        self.reap_finished()
        return [
            {
                "stream_id": s.config.stream_id,
                "source": str(s.config.source),
                "is_running": s.is_running,
                "metrics": s.get_metrics(),
            }
            for s in self.streams.values()
        ]

    def reap_finished(self) -> int:
        """Remove streams whose inference loop has ended, returning the count.

        A stream whose source reached EOF or errored finishes its thread and
        sets ``is_running = False`` (and closes its own DB row via
        ``_finalize_session``), but lingers in the registry until reaped.
        Reaping drops it so ``list_streams`` reflects reality and its stream_id
        can be reused. Its event history is intentionally retained in the
        EventStore, which outlives the live stream.

        Must be called only from the request/event-loop thread — the sole
        mutator of ``self.streams`` — so no lock is needed. The joined threads
        are already ending, so the bounded join returns effectively immediately.
        """
        with self._streams_lock:
            finished = [
                sid
                for sid, st in self.streams.items()
                if sid not in self._stopping and not st.is_running
            ]
            finished_streams = [self.streams.pop(stream_id) for stream_id in finished]
        for stream in finished_streams:
            if stream is not None and stream.thread is not None:
                stream.thread.join(timeout=1.0)
        if finished:
            logger.info(f"Reaped finished streams: {finished}")
        return len(finished)

    def get_stream_detections(self, stream_id: str) -> List[Detection]:
        """Get latest detections from a stream."""
        if stream_id not in self.streams:
            raise ValueError(f"Stream {stream_id} not found")

        return self.streams[stream_id].get_latest_detections()

    def get_stream_frame(self, stream_id: str, timeout: float = 1.0) -> Optional[dict]:
        """Get next output frame from a stream."""
        if stream_id not in self.streams:
            raise ValueError(f"Stream {stream_id} not found")

        return self.streams[stream_id].get_output_frame(timeout)

    def get_stream_events(
        self, stream_id: str, limit: Optional[int] = None
    ) -> List[dict]:
        """Return recent events for a stream, newest-first.

        Reads only from the in-memory event store (no database), so history is
        available even after a stream has stopped. Returns an empty list when
        the stream has no buffered history — this never raises for an unknown
        stream id, since event history is independent of the live stream set.
        """
        buffer = self.event_store.get(stream_id)
        if buffer is None:
            return []
        return buffer.latest(limit=limit)

    def get_stream_regions(self, stream_id: str) -> Optional[dict]:
        """Return the scene-region geometry configured on a stream, or None if
        the stream is not active. Regions are fixed at stream start, so this is
        exactly what the event engine is using.
        """
        stream = self.streams.get(stream_id)
        if stream is None:
            return None
        cfg = stream.config
        return {
            "stream_id": stream_id,
            "zones": [
                {"name": z.name, "polygon": [list(point) for point in z.polygon]}
                for z in cfg.zones
            ],
            "lines": [
                {
                    "name": ln.name,
                    "start": list(ln.start),
                    "end": list(ln.end),
                    "positive_label": ln.positive_label,
                    "negative_label": ln.negative_label,
                }
                for ln in cfg.lines
            ],
            "dwell_seconds": cfg.dwell_seconds,
            "width": cfg.width,
            "height": cfg.height,
        }

    def reconfigure_stream(
        self,
        stream_id: str,
        zones: Tuple[Zone, ...],
        lines: Tuple[CrossingLine, ...],
        dwell_seconds: float,
    ) -> dict:
        """Apply new scene geometry to a running stream; return its new regions.

        Raises ValueError if the stream is unknown or not running (the caller
        maps that to an HTTP status). Reaps finished streams first so a stale
        entry cannot be reconfigured.
        """
        self.reap_finished()
        stream = self.streams.get(stream_id)
        if stream is None:
            raise ValueError(f"Stream {stream_id} not found")
        if not stream.is_running:
            raise ValueError(f"Stream {stream_id} is not running")
        stream.reconfigure(zones, lines, dwell_seconds)
        return self.get_stream_regions(stream_id)
