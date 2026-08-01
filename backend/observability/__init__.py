"""
Observability subsystem for the OmniTrack platform.

Provides structured JSON logging, correlation-id propagation, Prometheus
metrics, a consistent exception hierarchy, and readiness/liveness probing —
all thread-safe, low-overhead, and non-blocking, with no changes to any public
API or WebSocket contract.

Import the pieces you need from the submodules; this package intentionally
keeps a shallow, explicit surface.
"""

from . import correlation, errors, metrics, readiness
from .logging import configure_logging, get_logger, shutdown_logging

__all__ = [
    "correlation",
    "errors",
    "metrics",
    "readiness",
    "configure_logging",
    "get_logger",
    "shutdown_logging",
]
