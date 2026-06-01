import logging
import time
from abc import ABC, abstractmethod
from typing import Generator, Optional

import cv2
import numpy as np

from .config import FrameSourceConfig
from .types import Frame


logger = logging.getLogger(__name__)


class FrameSource(ABC):
    """
    Abstract base class for frame input sources.

    Supports multiple input types (webcam, RTSP, files, streams).
    Extensibility: Subclass to add KafkaFrameSource, RTSPFrameSource, etc.
    in Phase 2+ for distributed ingestion.
    """

    @abstractmethod
    def open(self) -> None:
        """Initialize the frame source."""
        pass

    @abstractmethod
    def read(self) -> Generator[Frame, None, None]:
        """Yield frames from the source."""
        pass

    @abstractmethod
    def is_open(self) -> bool:
        """Check if source is active and healthy."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close and cleanup resources."""
        pass

    def __enter__(self) -> "FrameSource":
        """Context manager entry."""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()


class WebcamFrameSource(FrameSource):
    """
    Webcam input source using OpenCV VideoCapture.

    Features:
    - Configurable resolution and FPS
    - Error handling and retry logic
    - Graceful degradation on camera failure
    - FPS tracking (actual vs requested)
    """

    def __init__(self, config: FrameSourceConfig) -> None:
        self.config = config
        self.capture: Optional[cv2.VideoCapture] = None
        self.frame_id = 0
        self.is_initialized = False

    def open(self) -> None:
        """Initialize camera with retry logic."""
        for attempt in range(self.config.retry_attempts):
            try:
                logger.info(
                    f"Attempting to open webcam {self.config.source} "
                    f"(attempt {attempt + 1}/{self.config.retry_attempts})"
                )

                self.capture = cv2.VideoCapture(self.config.source)

                if not self.capture.isOpened():
                    raise RuntimeError("Camera not opened")

                self._configure_camera()
                self.is_initialized = True
                logger.info("Webcam initialized successfully")
                return

            except Exception as e:
                logger.warning(f"Failed to open camera (attempt {attempt + 1}): {e}")
                if self.capture is not None:
                    self.capture.release()
                    self.capture = None

                if attempt < self.config.retry_attempts - 1:
                    logger.info(
                        f"Retrying in {self.config.retry_delay_sec}s..."
                    )
                    time.sleep(self.config.retry_delay_sec)

        raise RuntimeError(
            f"Failed to open camera after {self.config.retry_attempts} attempts"
        )

    def _configure_camera(self) -> None:
        """Set camera resolution and FPS."""
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        self.capture.set(cv2.CAP_PROP_FPS, self.config.fps)

        actual_width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = int(self.capture.get(cv2.CAP_PROP_FPS))

        logger.debug(
            f"Camera configured: {actual_width}x{actual_height} @ {actual_fps} FPS"
        )

    def read(self) -> Generator[Frame, None, None]:
        """
        Generator yielding frames from camera.

        Handles read failures gracefully by skipping frames and continuing.
        """
        if not self.is_initialized:
            raise RuntimeError("Frame source not opened. Use context manager or call open()")

        frame_times = []
        max_history = 30  # Calculate FPS over last 30 frames

        logger.info("Starting frame capture")

        try:
            while self.is_open():
                ret, data = self.capture.read()

                if not ret or data is None:
                    logger.warning("Failed to read frame, skipping...")
                    continue

                # Resize frame if needed
                if (
                    data.shape[1] != self.config.width
                    or data.shape[0] != self.config.height
                ):
                    data = cv2.resize(data, (self.config.width, self.config.height))

                timestamp = time.time()
                frame_times.append(timestamp)
                if len(frame_times) > max_history:
                    frame_times.pop(0)

                frame = Frame(
                    timestamp=timestamp,
                    frame_id=self.frame_id,
                    data=data,
                )

                self.frame_id += 1
                yield frame

        except Exception as e:
            logger.error(f"Error reading frames: {e}", exc_info=True)
            raise

    def is_open(self) -> bool:
        """Check if camera is open and healthy."""
        return self.is_initialized and self.capture is not None and self.capture.isOpened()

    def close(self) -> None:
        """Release camera resources."""
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        self.is_initialized = False
        logger.info("Webcam closed")


class VideoFileFrameSource(FrameSource):
    """
    Play local video files (.mp4, .avi, .mkv, etc.).

    Features:
    - Handle EOF gracefully (stop yielding, caller loop exits)
    - FPS matching: if native FPS ≠ config.fps, add sleep() delays
    - Resolution handling: resize to config dimensions
    - Metadata logging on open
    - Error handling: missing file, corrupted file, invalid format
    """

    def __init__(self, config: FrameSourceConfig) -> None:
        self.config = config
        self.capture: Optional[cv2.VideoCapture] = None
        self.frame_id = 0
        self.is_initialized = False
        self.native_fps = 30.0
        self.total_frames = 0

    def open(self) -> None:
        """Initialize video file."""
        try:
            file_path = str(self.config.source)
            logger.info(f"Opening video file: {file_path}")

            self.capture = cv2.VideoCapture(file_path)

            if not self.capture.isOpened():
                raise RuntimeError(f"Failed to open video file: {file_path}")

            self._load_video_metadata()
            self.is_initialized = True
            logger.info(
                f"Video loaded: {self.config.width}x{self.config.height} @ "
                f"{self.native_fps:.1f} fps, {self.total_frames} frames "
                f"({self.total_frames/self.native_fps:.1f}s duration)"
            )

        except Exception as e:
            logger.error(f"Error opening video file: {e}", exc_info=True)
            if self.capture is not None:
                self.capture.release()
                self.capture = None
            raise

    def _load_video_metadata(self) -> None:
        """Extract video properties."""
        self.native_fps = self.capture.get(cv2.CAP_PROP_FPS)
        if self.native_fps <= 0:
            self.native_fps = 30.0
            logger.warning("Could not detect FPS, using default 30 fps")

        self.total_frames = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))

        # Set resolution
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)

    def read(self) -> Generator[Frame, None, None]:
        """
        Generator yielding frames from video file.

        Handles EOF gracefully. If config.fps differs from native FPS,
        adds delays to match requested playback speed.
        """
        if not self.is_initialized:
            raise RuntimeError("Frame source not opened. Use context manager or call open()")

        logger.info("Starting video playback")

        fps_ratio = self.native_fps / max(self.config.fps, 1.0)
        sleep_time = (1.0 / max(self.config.fps, 1.0)) if self.config.fps > 0 else 0.0

        try:
            while self.is_open():
                ret, data = self.capture.read()

                if not ret or data is None:
                    logger.info(f"End of video file ({self.frame_id} frames processed)")
                    break

                # Resize frame if needed
                if (
                    data.shape[1] != self.config.width
                    or data.shape[0] != self.config.height
                ):
                    data = cv2.resize(data, (self.config.width, self.config.height))

                timestamp = time.time()

                frame = Frame(
                    timestamp=timestamp,
                    frame_id=self.frame_id,
                    data=data,
                )

                self.frame_id += 1

                # Match requested FPS if different from native FPS
                if sleep_time > 0:
                    time.sleep(sleep_time)

                yield frame

        except Exception as e:
            logger.error(f"Error reading video file: {e}", exc_info=True)
            raise

    def is_open(self) -> bool:
        """Check if video file is open."""
        return self.is_initialized and self.capture is not None and self.capture.isOpened()

    def close(self) -> None:
        """Release video resources."""
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        self.is_initialized = False
        logger.info("Video file closed")


class RTSPFrameSource(FrameSource):
    """
    Stream from RTSP/IP cameras with automatic reconnection.

    Features:
    - Reconnection on stream drop with configurable exponential backoff
    - Stream health check: if no frames for N seconds, reconnect
    - Skip bad frames gracefully, continue stream
    - Minimal buffering (CAP_PROP_BUFFERSIZE = 1) for low latency
    - Configurable backoff strategy (initial, max, factor)

    Backoff example (defaults: 1s initial, 2.0x factor, 30s max):
    - Attempt 1: wait 1s
    - Attempt 2: wait 2s
    - Attempt 3: wait 4s
    - Attempt 4: wait 8s
    - Attempt 5: wait 16s
    - Attempt 6: wait 30s (capped)
    - Attempt 7+: wait 30s
    """

    def __init__(self, config: FrameSourceConfig) -> None:
        self.config = config
        self.capture: Optional[cv2.VideoCapture] = None
        self.frame_id = 0
        self.is_initialized = False
        self._last_frame_time = 0.0
        self._reconnect_attempt = 0

    def open(self) -> None:
        """Connect to RTSP stream with retry logic."""
        self._reconnect_attempt = 0
        self._connect_to_stream()

    def _connect_to_stream(self) -> None:
        """Attempt to connect to RTSP stream."""
        for attempt in range(self.config.retry_attempts):
            try:
                rtsp_url = str(self.config.source)
                logger.info(f"Connecting to RTSP stream: {rtsp_url} (attempt {attempt + 1})")

                self.capture = cv2.VideoCapture(rtsp_url)

                # Minimize buffering for low latency
                self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                # Test connection with first frame read attempt
                ret, frame = self.capture.read()

                if not ret or frame is None:
                    raise RuntimeError("Failed to read initial frame from stream")

                self._last_frame_time = time.time()
                self.is_initialized = True
                self._reconnect_attempt = 0

                logger.info(f"Connected to RTSP stream: {rtsp_url}")
                return

            except Exception as e:
                logger.warning(f"Failed to connect (attempt {attempt + 1}): {e}")

                if self.capture is not None:
                    self.capture.release()
                    self.capture = None

                if attempt < self.config.retry_attempts - 1:
                    backoff_wait = self._compute_backoff(attempt)
                    logger.info(f"Reconnecting in {backoff_wait:.1f}s...")
                    time.sleep(backoff_wait)

        raise RuntimeError(
            f"Failed to connect to RTSP stream after {self.config.retry_attempts} attempts"
        )

    def _compute_backoff(self, attempt: int) -> float:
        """Compute exponential backoff with configurable parameters."""
        backoff = self.config.backoff_initial_sec * (
            self.config.backoff_factor ** attempt
        )
        return min(backoff, self.config.backoff_max_sec)

    def read(self) -> Generator[Frame, None, None]:
        """
        Generator yielding frames from RTSP stream.

        Automatically reconnects on stream failure or timeout.
        """
        if not self.is_initialized:
            raise RuntimeError("Frame source not opened. Use context manager or call open()")

        logger.info("Starting RTSP stream capture")

        try:
            while True:
                # Check stream health
                if not self._is_stream_healthy():
                    logger.warning("Stream timeout detected, attempting reconnection...")
                    self._handle_reconnection()
                    continue

                ret, data = self.capture.read()

                if not ret or data is None:
                    logger.warning("Failed to read frame from stream, attempting reconnection...")
                    self._handle_reconnection()
                    continue

                self._last_frame_time = time.time()

                # Resize frame if needed
                if (
                    data.shape[1] != self.config.width
                    or data.shape[0] != self.config.height
                ):
                    data = cv2.resize(data, (self.config.width, self.config.height))

                timestamp = time.time()

                frame = Frame(
                    timestamp=timestamp,
                    frame_id=self.frame_id,
                    data=data,
                )

                self.frame_id += 1
                yield frame

        except Exception as e:
            logger.error(f"Error reading RTSP stream: {e}", exc_info=True)
            self.is_initialized = False
            raise

    def _is_stream_healthy(self) -> bool:
        """Check if stream is still active (not idle too long)."""
        if self._last_frame_time == 0:
            return True

        idle_time = time.time() - self._last_frame_time
        return idle_time < self.config.stream_timeout_sec

    def _handle_reconnection(self) -> None:
        """Attempt to reconnect to the stream."""
        self.is_initialized = False

        if self.capture is not None:
            self.capture.release()
            self.capture = None

        # Exponential backoff for reconnection attempts
        if self._reconnect_attempt >= self.config.retry_attempts:
            logger.error("Max reconnection attempts exceeded")
            raise RuntimeError("RTSP stream disconnected and max reconnection attempts exceeded")

        backoff_wait = self._compute_backoff(self._reconnect_attempt)
        self._reconnect_attempt += 1

        logger.info(
            f"Reconnecting to RTSP stream "
            f"(attempt {self._reconnect_attempt}/{self.config.retry_attempts}, "
            f"waiting {backoff_wait:.1f}s)..."
        )
        time.sleep(backoff_wait)

        try:
            self._connect_to_stream()
        except RuntimeError:
            if self._reconnect_attempt >= self.config.retry_attempts:
                raise
            # Will retry on next failed read

    def is_open(self) -> bool:
        """Check if RTSP stream is connected."""
        return self.is_initialized and self.capture is not None and self.capture.isOpened()

    def close(self) -> None:
        """Close RTSP stream."""
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        self.is_initialized = False
        logger.info("RTSP stream closed")


def get_frame_source(config: FrameSourceConfig) -> FrameSource:
    """
    Auto-detect source type and instantiate appropriate FrameSource.

    Detection logic:
    - source == 0 → WebcamFrameSource (default device)
    - source starts with rtsp:// or rtsps:// → RTSPFrameSource
    - otherwise → VideoFileFrameSource (file path)

    Args:
        config: FrameSourceConfig with source specification

    Returns:
        WebcamFrameSource, VideoFileFrameSource, or RTSPFrameSource

    Raises:
        ValueError: source type cannot be determined or invalid RTSP URL
        FileNotFoundError: video file doesn't exist
    """
    from pathlib import Path

    # Webcam: numeric ID (typically 0)
    if config.source == 0:
        logger.info("Auto-detected: Webcam input (source=0)")
        return WebcamFrameSource(config)

    source_str = str(config.source).strip().lower()

    # RTSP stream: URL with rtsp:// or rtsps:// protocol
    if source_str.startswith(("rtsp://", "rtsps://")):
        logger.info(f"Auto-detected: RTSP stream - {config.source}")

        # Basic RTSP URL validation
        if not _is_valid_rtsp_url(source_str):
            raise ValueError(f"Invalid RTSP URL format: {config.source}")

        return RTSPFrameSource(config)

    # Video file: local file path
    file_path = Path(config.source)

    if not file_path.exists():
        raise FileNotFoundError(f"Video file not found: {config.source}")

    logger.info(f"Auto-detected: Video file - {config.source}")
    return VideoFileFrameSource(config)


def _is_valid_rtsp_url(url: str) -> bool:
    """Basic RTSP URL validation."""
    try:
        # Check for protocol separator and port
        return (
            url.count("://") == 1
            and ":" in url.split("://")[1]
            and len(url) > 10
        )
    except (IndexError, AttributeError):
        return False
