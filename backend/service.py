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
from typing import Optional, Dict, List
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
from .models import Detection as DetectionRecord, Metric, StreamSession, get_database_session
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

    def __init__(self, config: StreamConfig, db_session, event_buffer=None) -> None:
        self.config = config
        self.db = db_session

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

            # Record session in database
            try:
                session = StreamSession(
                    stream_id=self.config.stream_id,
                    source=str(self.config.source),
                    width=self.config.width,
                    height=self.config.height,
                    tracking_enabled=self.config.tracking_enabled,
                )
                self.db.add(session)
                self.db.commit()
            except Exception as db_err:
                self.db.rollback()
                logger.error(f"Database error while recording stream session: {db_err}")
                raise

            logger.info(f"Stream {self.config.stream_id} started")

        except Exception as e:
            logger.error(f"Failed to start stream {self.config.stream_id}: {e}")
            self.metrics.is_active = False
            self.metrics.error_message = str(e)
            raise

    def stop(self) -> None:
        """Stop the inference stream."""
        if not self.is_running:
            logger.warning(f"Stream {self.config.stream_id} not running")
            return

        self.is_running = False

        # Wait for thread to finish
        if self.thread is not None:
            self.thread.join(timeout=5.0)

        # Cleanup resources
        self._cleanup()

        # Update session in database
        try:
            session_record = self.db.query(StreamSession).filter_by(
                stream_id=self.config.stream_id
            ).first()
            if session_record:
                session_record.is_active = False
                session_record.ended_at = datetime.utcnow()
                session_record.total_frames = self.metrics.total_frames
                session_record.total_detections = self.metrics.total_detections
                self.db.commit()
        except Exception as db_err:
            self.db.rollback()
            logger.error(f"Database error while updating stream session: {db_err}")

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
            # tracking is enabled. Default config => geometry detectors inert.
            self.event_engine = EventEngine(
                stream_id=self.config.stream_id,
                config=EventEngineConfig(),
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
                    self._store_detections(result.detections, frame)

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
            self._cleanup()
            logger.info(f"Inference loop finished for {self.config.stream_id}")

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

    def _store_detections(self, detections: List[Detection], frame: Frame) -> None:
        """Persist detections to database."""
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
            self.db.add(record)

        # Batch commit every 100 frames
        if frame.frame_id % 100 == 0:
            try:
                self.db.commit()
            except Exception as db_err:
                self.db.rollback()
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


class InferenceService:
    """Service managing multiple inference streams."""

    def __init__(self, db_path: str = "inference_data.db", event_capacity: int = 1000) -> None:
        self.db = get_database_session(db_path)
        self.streams: Dict[str, InferenceStream] = {}
        # Bounded in-memory event history per stream (persists across stop).
        self.event_store = EventStore(capacity=event_capacity)

    def start_stream(self, config: StreamConfig) -> StreamMetrics:
        """Start a new inference stream."""
        if config.stream_id in self.streams:
            raise ValueError(f"Stream {config.stream_id} already exists")

        stream = InferenceStream(
            config,
            self.db,
            event_buffer=self.event_store.get_or_create(config.stream_id),
        )
        stream.start()
        self.streams[config.stream_id] = stream

        return stream.get_metrics()

    def stop_stream(self, stream_id: str) -> None:
        """Stop an inference stream."""
        if stream_id not in self.streams:
            raise ValueError(f"Stream {stream_id} not found")

        self.streams[stream_id].stop()
        del self.streams[stream_id]

    def get_stream_metrics(self, stream_id: str) -> StreamMetrics:
        """Get metrics for a specific stream."""
        if stream_id not in self.streams:
            raise ValueError(f"Stream {stream_id} not found")

        return self.streams[stream_id].get_metrics()

    def list_streams(self) -> List[dict]:
        """List all active streams."""
        return [
            {
                "stream_id": s.config.stream_id,
                "source": str(s.config.source),
                "is_running": s.is_running,
                "metrics": s.get_metrics(),
            }
            for s in self.streams.values()
        ]

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
