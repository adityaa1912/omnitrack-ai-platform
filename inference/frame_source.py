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
