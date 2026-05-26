"""
Real-time YOLOv8 object detection pipeline.

Entry point and main orchestration logic. Composes detection, frame input,
and visualization components using clean dependency injection and context managers.

Usage:
    python -m inference.main --source 0 --conf 0.5 --device cpu

Extensibility: Replace WebcamFrameSource with KafkaFrameSource in Phase 2
for distributed multi-stream ingestion.
"""

import logging
import signal
import sys
import threading
from typing import Optional

import cv2

from .config import AppConfig
from .detector import Detector
from .frame_source import WebcamFrameSource
from .logger import get_logger, setup_logging
from .visualizer import FpsCounter, Visualizer


logger = get_logger(__name__)


class ShutdownHandler:
    """
    Graceful shutdown coordinator.

    Handles SIGINT and SIGTERM signals to enable clean resource cleanup.
    Extensibility: In Phase 5, integrate with health check endpoints for
    Kubernetes pod lifecycle (preStop hooks).
    """

    def __init__(self, timeout_sec: float = 5.0) -> None:
        self.shutdown_event = threading.Event()
        self.timeout_sec = timeout_sec
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register signal handlers."""
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame) -> None:
        """Handle shutdown signals."""
        signal_name = signal.Signals(signum).name
        logger.info(f"Received {signal_name}, initiating graceful shutdown...")
        self.shutdown_event.set()

    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested."""
        return self.shutdown_event.is_set()

    def wait_for_shutdown(self) -> bool:
        """Wait for shutdown signal with timeout."""
        return self.shutdown_event.wait(timeout=self.timeout_sec)


def main() -> int:
    """
    Main inference pipeline.

    Returns:
        Exit code (0 for success, non-zero for errors)
    """
    try:
        # 1. Load configuration from CLI arguments
        config = AppConfig.from_args()

        # 2. Setup logging
        setup_logging(
            level=config.log_level,
            debug=config.debug,
        )
        logger.info("=" * 60)
        logger.info("YOLOv8 Real-Time Object Detection Pipeline")
        logger.info("=" * 60)
        logger.debug(f"Configuration: {config}")

        # 3. Setup graceful shutdown handler
        shutdown = ShutdownHandler(timeout_sec=config.graceful_shutdown_timeout_sec)

        # 4. Initialize components (with context managers for resource cleanup)
        logger.info("Initializing inference pipeline...")

        with WebcamFrameSource(config.frame_source_config) as frame_source:
            with Detector(config.detector_config) as detector:
                visualizer = Visualizer(config.visualizer_config)
                fps_counter = FpsCounter()

                logger.info("Pipeline ready. Starting inference...")
                logger.info("Press 'q' to quit or Ctrl+C for graceful shutdown")

                # 5. Main inference loop
                frame_count = 0
                try:
                    for frame in frame_source.read():
                        # Check for shutdown signal
                        if shutdown.is_shutdown_requested():
                            logger.info("Shutdown signal received, exiting...")
                            break

                        # Run inference
                        result = detector.predict(frame)
                        fps_counter.update(result.inference_time_ms)
                        frame_count += 1

                        # Render results
                        output_frame = visualizer.render(
                            frame.data,
                            result,
                            fps=fps_counter.fps,
                        )

                        # Display frame
                        cv2.imshow("YOLOv8 Detection", output_frame)

                        # Check for 'q' key to quit
                        key = cv2.waitKey(1) & 0xFF
                        if key == ord("q"):
                            logger.info("User quit requested")
                            break

                        # Log periodic stats
                        if frame_count % 100 == 0:
                            logger.info(
                                f"Processed {frame_count} frames | "
                                f"FPS: {fps_counter.fps:.1f} | "
                                f"Inference: {result.inference_time_ms:.1f}ms | "
                                f"Detections: {result.num_detections}"
                            )

                except KeyboardInterrupt:
                    logger.info("Interrupted")
                except Exception as e:
                    logger.error(f"Inference error: {e}", exc_info=True)
                    return 1

        logger.info(f"Inference complete. Processed {frame_count} frames")
        logger.info("=" * 60)
        return 0

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1

    finally:
        cv2.destroyAllWindows()
        logger.info("Cleanup complete")


if __name__ == "__main__":
    sys.exit(main())
