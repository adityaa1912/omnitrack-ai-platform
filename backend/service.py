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
from typing import Optional, Dict, List, Tuple, Callable, Any
from queue import Queue, Full, Empty
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
from inference.frame_pool import get_frame_pool
from inference.events import EventEngine, EventEngineConfig
from inference.events.regions import CrossingLine, Zone
from .models import Detection as DetectionRecord, StreamSession, create_session_factory
from .event_store import EventStore
from .scheduler import InferenceScheduler
from .batching import BatchManager, detector_signature
from .model_manager import get_model_manager, shutdown_model_manager
from .observability import correlation, metrics
from .observability.errors import log_error
from .observability.logging import get_logger
from .analytics.aggregator import AnalyticsAggregator
from .settings import get_settings


settings = get_settings()

logger = get_logger(__name__, component="inference.stream")


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
    model_path: str = "yolov8n.pt"
    inference_device: str = "cpu"
    source_retry_attempts: int = 3
    source_retry_delay_seconds: float = 5.0
    source_stream_timeout_seconds: float = 10.0
    source_backoff_initial_seconds: float = 1.0
    source_backoff_max_seconds: float = 30.0
    source_backoff_factor: float = 2.0
    stop_timeout_seconds: float = 5.0
    frame_queue_capacity: int = 30

    # Performance tuning (defaults preserve current behavior). See Settings.
    inference_target_fps: float = 0.0  # 0 = uncapped
    frame_skip: int = 0  # 0 = process every frame
    inference_width: int = 0  # 0 = source-native
    inference_height: int = 0  # 0 = source-native
    enable_fp16: bool = False
    adaptive_frame_drop: bool = True
    # Inference backend: "torch" (default) or "onnx". See Settings.
    inference_provider: str = "torch"
    # Auto mode only: benchmark providers once and pick the fastest. See Settings.
    inference_benchmark_enabled: bool = False
    inference_benchmark_runs: int = 5

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

    def __init__(self, config: StreamConfig, session_factory, event_buffer=None, scheduler=None, batch_manager=None, model_manager=None) -> None:
        self.config = config
        # Thread-safe session registry (scoped_session). Each thread that writes
        # obtains its OWN session via `self._session_factory()` and releases it
        # with `.remove()` when done; a Session is never shared across threads.
        self._session_factory = session_factory

        # Optional central scheduler. When set, capture is decoupled from the
        # infer/track/render stage: this stream's frames are submitted to the
        # scheduler's pinned worker instead of processed inline. None ==> the
        # legacy one-thread-per-stream loop, byte-for-byte unchanged.
        self._scheduler = scheduler

        # Optional dynamic-batching manager. When set, this stream shares a
        # detector with other streams of the same detector signature and routes
        # inference through a coordinator that fuses concurrent forward passes.
        # None ==> this stream owns a private Detector and infers one frame at a
        # time, exactly as before.
        self._batch_manager = batch_manager
        # Optional process-wide model pool. Used for the non-batched path to
        # share (and reference-count) detectors across streams; the batched path
        # shares through the batch manager, which delegates to this same pool.
        self._model_manager = model_manager
        self._coordinator = None
        self._detector_signature = None

        # Components
        self.frame_source = None
        self.detector = None
        self.tracker = None
        self.visualizer = None
        self.event_engine: Optional[EventEngine] = None

        # Bounded in-memory event sink (ring buffer owned by the service).
        self.event_buffer = event_buffer
        self.recording_manager = None

        # State
        self.is_running = False
        self.thread: Optional[threading.Thread] = None

        # Metrics
        self.metrics = StreamMetrics(stream_id=config.stream_id)
        self.inference_times = []
        self.latest_detections: List[Detection] = []
        self.latest_frame: Optional[Frame] = None

        # Output queue for frames/detections
        self.output_queue: Queue = Queue(maxsize=config.frame_queue_capacity)

    def start(self) -> None:
        """Start the inference stream in a background thread."""
        if self.is_running:
            logger.warning(f"Stream {self.config.stream_id} already running")
            return

        try:
            self._initialize_components()
            self.is_running = True
            # Initialize recording manager if enabled
            if getattr(settings, "recording_enabled", False) and getattr(settings, "recording_storage_path", None):
                from backend.recording.manager import RecordingManager
                from backend.recording.storage import LocalFileStorageProvider
                storage = LocalFileStorageProvider(settings.recording_storage_path)
                self.recording_manager = RecordingManager(self._session_factory, storage, settings)
                self.recording_manager.start_recording(self.config.stream_id)
            if self._scheduler is not None:
                # Decoupled path: pin this stream to a scheduler worker and run a
                # capture-only thread that submits frames for that worker to
                # process. The processing (infer/track/render/enqueue/events) is
                # the SAME `_process_frame` the legacy loop uses — no duplication.
                self._scheduler.register(
                    self.config.stream_id, self._process_frame, owner=self
                )
                self.thread = threading.Thread(target=self._run_capture, daemon=True)
            else:
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
        if self.recording_manager:
            try:
                self.recording_manager.stop_recording(self.config.stream_id)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Error stopping recording manager for {self.config.stream_id}: {exc}")
        if self.thread is not None:
            self.thread.join(timeout=self.config.stop_timeout_seconds)
        # In scheduler mode the capture thread's `finally` already runs
        # `_cleanup` AFTER waiting for the pinned worker's in-flight frame to
        # finish (see `_run_capture` / `InferenceScheduler.unregister`). Running
        # `_cleanup` here too could tear down the detector/frame source while a
        # worker is still inside `_process_frame`; the legacy loop has no such
        # split, so it still cleans up here as before.
        if self._scheduler is None:
            self._cleanup()
        logger.info(f"Stream {self.config.stream_id} stopped")

    def _initialize_components(self) -> None:
        """Initialize detector, tracker, visualizer, frame source."""
        detector_config = DetectorConfig(
            model_name=self.config.model_path,
            confidence_threshold=self.config.confidence_threshold,
            device=self.config.inference_device,
            inference_width=self.config.inference_width,
            inference_height=self.config.inference_height,
            enable_fp16=self.config.enable_fp16,
            inference_provider=self.config.inference_provider,
            benchmark_enabled=self.config.inference_benchmark_enabled,
            benchmark_runs=self.config.inference_benchmark_runs,
        )
        load_start = time.perf_counter()
        if self._batch_manager is not None:
            # Share one detector across all streams of this signature and route
            # inference through the coordinator (which fuses concurrent forward
            # passes). The detector is built/loaded once per signature.
            self._detector_signature = detector_signature(detector_config)
            self.detector, self._coordinator = self._batch_manager.acquire(
                detector_config
            )
        elif self._model_manager is not None:
            # Non-batched sharing: acquire the reference-counted shared detector
            # from the process-wide pool. Inference stays one frame at a time
            # (no coordinator), but the model is shared across identical streams.
            self._detector_signature = detector_signature(detector_config)
            self.detector = self._model_manager.acquire(detector_config)
        else:
            self.detector = Detector(detector_config)
        provider_name = getattr(self.detector.provider, "name", "unknown")
        metrics.INFERENCE_PROVIDER_LOAD_TIME.labels(
            provider=provider_name
        ).set(time.perf_counter() - load_start)
        metrics.INFERENCE_PROVIDER.labels(provider=provider_name).set(1)

        frame_source_config = FrameSourceConfig(
            source=self.config.source,
            width=self.config.width,
            height=self.config.height,
            fps=self.config.fps,
            retry_attempts=self.config.source_retry_attempts,
            retry_delay_sec=self.config.source_retry_delay_seconds,
            stream_timeout_sec=self.config.source_stream_timeout_seconds,
            backoff_initial_sec=self.config.source_backoff_initial_seconds,
            backoff_max_sec=self.config.source_backoff_max_seconds,
            backoff_factor=self.config.source_backoff_factor,
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
        if self._coordinator is not None:
            # Shared detector: drop this stream's reference; the manager stops
            # and frees the model only when the last stream of this signature
            # goes away. Never null a model other streams may still be using.
            if self._batch_manager is not None and self._detector_signature is not None:
                self._batch_manager.release(self._detector_signature)
            self._coordinator = None
        elif self._model_manager is not None and self._detector_signature is not None:
            # Non-batched shared detector: drop this stream's pool reference. The
            # pool keeps the model warm (or evicts it under cap) — never null it
            # here, as other streams of this signature may still hold it.
            self._model_manager.release(self._detector_signature)
            self.detector = None
        elif self.detector is not None:
            self.detector.model = None

    def _run(self) -> None:
        """Main inference loop (runs in background thread)."""
        # Pin this stream's identity to the thread's correlation context so
        # every log record emitted here carries its stream_id.
        correlation.bind_stream_context(stream_id=self.config.stream_id)
        # This thread's own database session (see `create_session_factory`);
        # confined to this thread and released in the `finally` below.
        db = self._session_factory()
        # Performance-tuning locals (immutable config; read once, no per-frame
        # attribute lookups). Defaults preserve current behavior exactly.
        cfg = self.config
        frame_skip = max(cfg.frame_skip, 0)
        target_fps = cfg.inference_target_fps
        min_interval = (1.0 / target_fps) if target_fps and target_fps > 0 else 0.0
        skip_mod = frame_skip + 1
        last_infer_at = 0.0
        try:
            logger.info(f"Inference loop started for {self.config.stream_id}")

            # Time the blocking capture separately: read() is a generator, so we
            # wrap it to measure how long each next() (camera grab) takes.
            frame_iter = self.frame_source.read()
            while self.is_running:
                capture_start = time.perf_counter()
                try:
                    frame = next(frame_iter)
                except StopIteration:
                    break
                capture_ms = (time.perf_counter() - capture_start) * 1000.0
                metrics.observe_pipeline_stage(
                    self.config.stream_id, "capture", capture_ms
                )

                if not self.is_running:
                    break

                # Adaptive frame skip: process 1 in (frame_skip+1) frames. The
                # skipped frames are simply not inferred on (newest wins because
                # the source keeps delivering current frames).
                if frame_skip and (frame.frame_id % skip_mod) != 0:
                    continue

                # Inference FPS cap: if we inferred too recently, skip this frame
                # so the model is not saturated and latency stays bounded.
                if min_interval > 0.0:
                    now = time.perf_counter()
                    if (now - last_infer_at) < min_interval:
                        continue

                frame_start = time.perf_counter()
                try:
                    # Process the frame inline (infer/track/render/enqueue/events)
                    # via the shared processor; it returns the post-inference
                    # timestamp used for the FPS cap.
                    last_infer_at = self._process_frame(frame, db)
                except Exception as e:
                    log_error(
                        logger,
                        e,
                        f"Error in inference loop for {self.config.stream_id}",
                        stream_id=self.config.stream_id,
                    )
                    metrics.STREAM_ERRORS_TOTAL.labels(
                        stream_id=self.config.stream_id
                    ).inc()
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
            correlation.clear_stream_context()
            logger.info(f"Inference loop finished for {self.config.stream_id}")

    def _run_capture(self) -> None:
        """Capture-only loop for the scheduler path (runs in background thread).

        Mirrors `_run`'s capture, frame-skip and FPS-cap gating exactly, but
        instead of processing a frame inline it submits it to the scheduler,
        whose pinned worker calls `_process_frame`. Capture is thus decoupled
        from inference so a slow stream cannot stall the capture loop. This
        thread owns a session solely to finalize the stream on exit; per-frame
        persistence happens on the worker's own session.
        """
        correlation.bind_stream_context(stream_id=self.config.stream_id)
        db = self._session_factory()
        cfg = self.config
        frame_skip = max(cfg.frame_skip, 0)
        target_fps = cfg.inference_target_fps
        min_interval = (1.0 / target_fps) if target_fps and target_fps > 0 else 0.0
        skip_mod = frame_skip + 1
        last_submit_at = 0.0
        try:
            logger.info(f"Capture loop started for {self.config.stream_id}")
            frame_iter = self.frame_source.read()
            while self.is_running:
                capture_start = time.perf_counter()
                try:
                    frame = next(frame_iter)
                except StopIteration:
                    break
                capture_ms = (time.perf_counter() - capture_start) * 1000.0
                metrics.observe_pipeline_stage(
                    self.config.stream_id, "capture", capture_ms
                )

                if not self.is_running:
                    break

                if frame_skip and (frame.frame_id % skip_mod) != 0:
                    continue

                if min_interval > 0.0:
                    now = time.perf_counter()
                    if (now - last_submit_at) < min_interval:
                        continue

                self._scheduler.submit(self.config.stream_id, frame)
                last_submit_at = time.perf_counter()

        finally:
            # Same finalize-before-terminal-flag ordering the reaper depends on.
            # Unregister after finalize so a lingering in-flight worker frame
            # finishes safely; the worker re-checks channel presence.
            self._finalize_session(db)
            self._session_factory.remove()
            self.is_running = False
            if self._scheduler is not None:
                self._scheduler.unregister(self.config.stream_id)
            self._cleanup()
            correlation.clear_stream_context()
            logger.info(f"Capture loop finished for {self.config.stream_id}")

    def _infer(self, frame: Frame):
        """Run inference for one frame, batching via the coordinator when set.

        The single seam between the per-frame pipeline and dynamic batching: with
        a coordinator, concurrent same-signature streams fuse into one forward
        pass; without one, this is the original direct single-frame call.
        """
        if self._coordinator is not None:
            return self._coordinator.predict(frame)
        return self.detector.predict(frame)

    def _process_frame(self, frame: Frame, db) -> float:
        """Process one captured frame end-to-end on the calling thread's session.

        Runs inference, tracking, rendering, output-queue enqueue (with adaptive
        drop) and event derivation, updating per-frame metrics. Returns the
        post-inference `perf_counter` timestamp used by the caller's FPS cap.

        Shared by the legacy inline loop (`_run`) and the scheduler workers, so
        the inference pipeline lives in exactly one place. It never handles its
        own termination: a raised exception propagates to the caller, which owns
        the per-frame error handling (mark failed, stop the stream).
        """
        cfg = self.config
        frame_start = time.perf_counter()
        # Run inference (model forward pass + result parsing).
        infer_start = time.perf_counter()
        result = self._infer(frame)
        last_infer_at = time.perf_counter()
        metrics.observe_pipeline_stage(
            self.config.stream_id,
            "inference",
            (last_infer_at - infer_start) * 1000.0,
        )
        self.latest_frame = frame
        self.metrics.total_frames += 1

        # Run tracking if enabled
        tracking_result = None
        if self.tracker is not None:
            track_start = time.perf_counter()
            tracking_result = self.tracker.update(
                result.detections,
                frame.frame_id,
                frame.timestamp,
            )
            metrics.observe_pipeline_stage(
                self.config.stream_id,
                "tracking",
                (time.perf_counter() - track_start) * 1000.0,
            )
            self.latest_detections = tracking_result.tracked_objects

            # Render with tracking
            render_start = time.perf_counter()
            output_frame = self.visualizer.render_tracked(
                frame.data,
                tracking_result,
                fps=self.metrics.fps,
            )
            metrics.observe_pipeline_stage(
                self.config.stream_id,
                "render",
                (time.perf_counter() - render_start) * 1000.0,
            )
        else:
            self.latest_detections = result.detections
            render_start = time.perf_counter()
            output_frame = self.visualizer.render(
                frame.data,
                result,
                fps=self.metrics.fps,
            )
            metrics.observe_pipeline_stage(
                self.config.stream_id,
                "render",
                (time.perf_counter() - render_start) * 1000.0,
            )

        # Update metrics
        self._update_metrics(result.inference_time_ms, len(result.detections))

        # Store detections in database
        self._store_detections(db, result.detections, frame)

        # Add to output queue. Adaptive drop: when the queue is full,
        # evict the stalest buffered frame and enqueue the newest, so
        # consumers always get the freshest frame and latency stays
        # bounded instead of growing. When adaptive drop is disabled
        # we fall back to dropping the new frame (prior behavior).
        enqueued = False
        try:
            self.output_queue.put_nowait({
                "frame": output_frame,
                "detections": result.detections,
                "timestamp": frame.timestamp,
            })
            enqueued = True
        except Full:
            if cfg.adaptive_frame_drop:
                try:
                    evicted = self.output_queue.get_nowait()  # evict stalest
                    get_frame_pool().release(evicted.get("frame"))
                    self.output_queue.put_nowait({
                        "frame": output_frame,
                        "detections": result.detections,
                        "timestamp": frame.timestamp,
                    })
                    enqueued = True
                except (Full, Empty):
                    enqueued = False
            if not enqueued:
                # Counted, not silent: a slow consumer never
                # back-pressures the inference loop.
                get_frame_pool().release(output_frame)
                metrics.DROPPED_FRAMES_TOTAL.labels(
                    stream_id=self.config.stream_id
                ).inc()

        # Push frame to recording manager (fail-safe, off the hot path).
        if self.recording_manager:
            try:
                self.recording_manager.push_frame(
                    self.config.stream_id,
                    {
                        "image": output_frame,
                        "width": getattr(self.config, "width", 640),
                        "height": getattr(self.config, "height", 480),
                        "fps": self.metrics.fps,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Error pushing frame to recording manager: {exc}")

        # Record per-frame pipeline metrics (queue depth sampled
        # after the put/drop so it reflects the current state).
        frame_latency_ms = (time.perf_counter() - frame_start) * 1000.0
        metrics.FRAME_QUEUE_DEPTH.labels(
            stream_id=self.config.stream_id
        ).set(self.output_queue.qsize())
        metrics.observe_inference(
            stream_id=self.config.stream_id,
            inference_time_ms=result.inference_time_ms,
            frame_latency_ms=frame_latency_ms,
            num_detections=len(result.detections),
        )

        # Derive events AFTER the frame has been queued, so event
        # generation sits off the frame-delivery path and can never
        # delay or block it. Guarded internally so it can never
        # break the inference loop.
        if tracking_result is not None:
            event_start = time.perf_counter()
            self._process_events(tracking_result)
            metrics.observe_pipeline_stage(
                self.config.stream_id,
                "event",
                (time.perf_counter() - event_start) * 1000.0,
            )
            if self._analytics is not None:
                for record in stored:
                    try:
                        self._analytics.handle_event(record)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            f"Analytics event handling failed for "
                            f"{self.config.stream_id}: {exc}"
                        )

        return last_infer_at

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
                stored = self.event_buffer.extend(event.to_dict() for event in events)
                # Record event throughput, partitioned by type and severity, and
                # stamp a correlation event id onto each stored record.
                for record in stored:
                    correlation.bind_event_id()
                    metrics.EVENTS_TOTAL.labels(
                        stream_id=self.config.stream_id,
                        event_type=str(record.get("event_type", "unknown")),
                        severity=str(record.get("severity", "unknown")),
                    ).inc()
                    # Trigger recording manager for this event type
                    if self.recording_manager:
                        try:
                            self.recording_manager.trigger_event(
                                self.config.stream_id,
                                str(record.get("event_type", "unknown")),
                                record,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.error(f"Error triggering recording event: {exc}")
        except Exception as exc:
            # Contained deliberately: event derivation must never kill the
            # inference loop. Logged structured with traceback, not swallowed.
            log_error(
                logger,
                exc,
                f"Event processing error for stream {self.config.stream_id}",
                stream_id=self.config.stream_id,
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

    def __init__(
        self,
        db_path: str = "inference_data.db",
        event_capacity: int = 1000,
        *,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_recycle_seconds: int = 1800,
        pool_pre_ping: bool = True,
        connect_max_attempts: int = 1,
        connect_retry_delay_seconds: float = 1.0,
        scheduler_enabled: bool = False,
        scheduler_workers: int = 2,
        scheduler_stream_queue_capacity: int = 2,
        batching_enabled: bool = False,
        batch_max_size: int = 8,
        batch_max_wait_ms: int = 10,
        model_manager_enabled: bool = False,
        model_manager_max_loaded: int = 4,
    ) -> None:
        # Thread-safe DB access: one engine + a scoped_session registry shared by
        # every stream thread, each of which uses its own thread-local session.
        # ``db_path`` may be a SQLite file path or a full SQLAlchemy URL.
        self.engine, self.Session = self._build_engine_with_retry(
            db_path,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_recycle_seconds=pool_recycle_seconds,
            pool_pre_ping=pool_pre_ping,
            connect_max_attempts=connect_max_attempts,
            connect_retry_delay_seconds=connect_retry_delay_seconds,
        )
        self.streams: Dict[str, InferenceStream] = {}
        # Stop joins run in a worker so the ASGI loop can close WebSockets.
        # Guard the registry against a concurrent ``reap_finished`` removing a
        # stream in the narrow interval after ``stop`` flips is_running.
        self._streams_lock = threading.RLock()
        self._stopping: set[str] = set()
        # Bounded in-memory event history per stream (persists across stop).
        self.event_store = EventStore(capacity=event_capacity)
        self._is_shutdown = False
        # Optional derived-event publisher (e.g. the Kafka event bus), subscribed
        # to each stream's event buffer at stream start. ``None`` = no bus. The
        # callable is fail-safe and never raises into the inference loop.
        self._event_publisher: Optional[Callable[[Dict[str, Any]], None]] = None
        # Optional analytics aggregator
        self._analytics: Optional[AnalyticsAggregator] = None
        self._alerts = None

        # Optional central scheduler. Built lazily on first use so the default
        # (disabled) deployment never constructs a worker pool at startup.
        self._scheduler: Optional[InferenceScheduler] = None
        self._scheduler_enabled = scheduler_enabled
        self._scheduler_workers = scheduler_workers
        self._scheduler_stream_queue_capacity = scheduler_stream_queue_capacity
        self._scheduler_lock = threading.Lock()

        # Optional dynamic batching. Built lazily on first use so the default
        # (disabled) deployment never constructs a batch manager at startup.
        self._batch_manager: Optional[BatchManager] = None
        self._batching_enabled = batching_enabled
        self._batch_max_size = batch_max_size
        self._batch_max_wait_ms = batch_max_wait_ms
        self._batch_manager_lock = threading.Lock()

        # Optional process-wide model manager. Built lazily on first use so the
        # default (disabled) deployment never constructs a pool at startup. When
        # enabled it backs both the batched path (via the batch manager) and the
        # non-batched path with one reference-counted, LRU-bounded model pool.
        self._model_manager = None
        self._model_manager_enabled = model_manager_enabled
        self._model_manager_max_loaded = model_manager_max_loaded
        self._model_manager_lock = threading.Lock()

    def _get_model_manager(self):
        """Return the lazy-built model manager, or None when it is disabled."""
        if not self._model_manager_enabled:
            return None
        with self._model_manager_lock:
            if self._model_manager is None:
                self._model_manager = get_model_manager(
                    self._model_manager_max_loaded
                )
            return self._model_manager

    def _get_batch_manager(self) -> Optional[BatchManager]:
        """Return the lazy-built batch manager, or None when batching is disabled."""
        if not self._batching_enabled:
            return None
        with self._batch_manager_lock:
            if self._batch_manager is None:
                self._batch_manager = BatchManager(
                    self._batch_max_size,
                    self._batch_max_wait_ms,
                    model_manager=self._get_model_manager(),
                )
            return self._batch_manager

    def _get_scheduler(self) -> Optional[InferenceScheduler]:
        """Return the lazy-built scheduler, or None when scheduling is disabled."""
        if not self._scheduler_enabled:
            return None
        with self._scheduler_lock:
            if self._scheduler is None:
                self._scheduler = InferenceScheduler(
                    self.Session,
                    num_workers=self._scheduler_workers,
                    stream_queue_capacity=self._scheduler_stream_queue_capacity,
                )
            return self._scheduler

    def set_event_publisher(
        self, publisher: Optional[Callable[[Dict[str, Any]], None]]
    ) -> None:
        """Register an optional derived-event publisher.

        The publisher is subscribed to every stream's event buffer created from
        this point on. Passing ``None`` (or a disabled publisher) leaves event
        handling unchanged. This is the single seam between the in-process event
        store and an external event bus; it does not alter EventBuffer semantics,
        stream ownership, or inference logic.
        """
        self._event_publisher = publisher

    def set_analytics(self, analytics: AnalyticsAggregator) -> None:
        """Register an optional analytics aggregator."""
        self._analytics = analytics

    def set_alerts(self, engine) -> None:
        self._alerts = engine

    @staticmethod
    def _build_engine_with_retry(
        db_path: str,
        *,
        pool_size: int,
        max_overflow: int,
        pool_recycle_seconds: int,
        pool_pre_ping: bool,
        connect_max_attempts: int,
        connect_retry_delay_seconds: float,
    ):
        """Create the engine/session factory, retrying transient connect failures.

        ``create_session_factory`` runs ``create_all`` against a live connection,
        so a database that is still starting (e.g. a PostgreSQL container not yet
        accepting connections) raises here. We retry a bounded number of times
        with a fixed delay, then re-raise so startup fails gracefully and the
        process surfaces a clear error instead of running against a dead DB.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, connect_max_attempts + 1):
            try:
                return create_session_factory(
                    db_path,
                    pool_size=pool_size,
                    max_overflow=max_overflow,
                    pool_recycle_seconds=pool_recycle_seconds,
                    pool_pre_ping=pool_pre_ping,
                )
            except Exception as exc:  # noqa: BLE001 - retried then re-raised
                last_exc = exc
                if attempt < connect_max_attempts:
                    logger.warning(
                        f"Database connect attempt {attempt}/"
                        f"{connect_max_attempts} failed: {exc}; retrying in "
                        f"{connect_retry_delay_seconds}s"
                    )
                    time.sleep(connect_retry_delay_seconds)
        logger.error(
            f"Database unavailable after {connect_max_attempts} attempt(s): "
            f"{last_exc}"
        )
        raise last_exc  # type: ignore[misc]

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
            scheduler=self._get_scheduler(),
            batch_manager=self._get_batch_manager(),
            model_manager=self._get_model_manager(),
        )
        # Subscribe the derived-event publisher (Kafka bus) to this stream's
        # buffer. Guarded so a misbehaving publisher can never block a start.
        if self._event_publisher is not None:
            try:
                self.event_store.get(config.stream_id).subscribe(self._event_publisher)
            except Exception as exc:  # noqa: BLE001 - bus must not affect streams
                logger.warning(
                    f"Could not subscribe event publisher for "
                    f"{config.stream_id}: {exc}"
                )
        # Subscribe the analytics aggregator so it receives every derived event.
        # Guarded so a misbehaving aggregator can never block a stream start.
        if self._analytics is not None:
            try:
                self.event_store.get(config.stream_id).subscribe(
                    self._analytics.handle_event
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"Could not subscribe analytics for "
                    f"{config.stream_id}: {exc}"
                )
        if self._alerts is not None:
            try:
                self.event_store.get(config.stream_id).subscribe(
                    self._alerts.handle_event
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"Could not subscribe alert engine for "
                    f"{config.stream_id}: {exc}"
                )
        stream.start()
        with self._streams_lock:
            self.streams[config.stream_id] = stream
            metrics.ACTIVE_STREAMS.set(len(self.streams))
        metrics.STREAMS_STARTED_TOTAL.inc()

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
                metrics.ACTIVE_STREAMS.set(len(self.streams))
            metrics.STREAMS_STOPPED_TOTAL.inc()

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
                log_error(
                    logger,
                    exc,
                    f"Error stopping stream {stream_id} during shutdown",
                    stream_id=stream_id,
                )

        # Stop the shared worker pool (if it was ever built) after every stream's
        # capture thread has been stopped, so no worker is processing a stream.
        if self._scheduler is not None:
            self._scheduler.stop()

        # Stop every batch coordinator (if batching was ever used) after all
        # streams have released their shared detectors.
        if self._batch_manager is not None:
            self._batch_manager.stop_all()

        # Unload the shared model pool (if it was ever built) last, after every
        # stream and coordinator has dropped its references, so the accelerator
        # memory held by pooled detectors is reclaimed at shutdown.
        if self._model_manager is not None:
            shutdown_model_manager()

        # Release the shutdown thread's session, then close all connections.
        self.Session.remove()
        self.engine.dispose()
        with self._streams_lock:
            metrics.ACTIVE_STREAMS.set(0)
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
            if finished:
                metrics.ACTIVE_STREAMS.set(len(self.streams))
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
