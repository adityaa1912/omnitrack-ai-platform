import logging
import sys
from typing import Optional


def setup_logging(
    level: str = "INFO",
    debug: bool = False,
    log_file: Optional[str] = None,
) -> None:
    """
    Configure logging for the inference pipeline.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
        debug: Enable verbose debug mode
        log_file: Optional file path for logging output

    Extensibility: In Phase 5, replace with OpenTelemetry instrumentation
    for distributed tracing and Prometheus metrics export.
    """
    if debug:
        level = "DEBUG"

    log_level = getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Suppress verbose third-party logging
    logging.getLogger("ultralytics").setLevel(logging.WARNING)
    logging.getLogger("cv2").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for a module."""
    return logging.getLogger(name)
