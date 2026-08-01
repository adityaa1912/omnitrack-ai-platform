"""
Consistent exception hierarchy and structured error reporting.

A single, small taxonomy of platform errors. Every error carries a stable
machine-readable ``code`` and an optional ``context`` mapping, so a raised
error can be logged once, structured, at the boundary that handles it — and
never swallowed silently.

Design rules
------------
* **Never swallow.** If an exception must be contained (e.g. to keep the
  inference loop alive), it is logged with full traceback *at the point of
  containment* via :func:`log_error`, then either re-raised or explicitly
  recorded. Silent ``except: pass`` is not permitted on new code.
* **Structured.** :func:`log_error` emits a JSON record (via the configured
  structured logger) carrying the error code, type, message, context and
  traceback, plus whatever correlation ids are bound in the current context.
* **Typed.** Callers raise the most specific subclass; handlers can catch the
  base :class:`OmniTrackError` to handle any platform error uniformly.

This module is dependency-free (no web imports) so the inference layer can use
it without pulling in FastAPI.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional


class OmniTrackError(Exception):
    """Base class for all platform errors.

    Attributes:
        code: Stable, machine-readable error code (e.g. ``stream_not_found``).
        message: Human-readable description.
        context: Optional structured key/value context for diagnostics.
    """

    #: Default code; subclasses override.
    code: str = "internal_error"

    def __init__(
        self,
        message: str = "",
        *,
        code: Optional[str] = None,
        context: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.context: dict[str, Any] = dict(context) if context else {}

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the error."""
        return {
            "error": self.code,
            "type": type(self).__name__,
            "message": self.message,
            "context": self.context,
        }


# ---------------------------------------------------------------------------
# Configuration / startup
# ---------------------------------------------------------------------------

class ConfigurationError(OmniTrackError):
    """Invalid or inconsistent runtime configuration."""

    code = "configuration_invalid"


class ModelUnavailableError(OmniTrackError):
    """The inference model could not be loaded or is not usable."""

    code = "model_unavailable"


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------

class StreamError(OmniTrackError):
    """Base class for stream lifecycle errors."""

    code = "stream_error"


class StreamNotFoundError(StreamError):
    """The referenced stream does not exist / is not active."""

    code = "stream_not_found"


class StreamAlreadyExistsError(StreamError):
    """A stream with the same id is already registered."""

    code = "stream_already_exists"


class StreamNotRunningError(StreamError):
    """The stream exists but is not in a running state."""

    code = "stream_not_running"


class StreamSourceError(StreamError):
    """The frame source failed to open or read."""

    code = "stream_source_error"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class DatabaseError(OmniTrackError):
    """A database operation failed."""

    code = "database_unavailable"


# ---------------------------------------------------------------------------
# Structured logging helper
# ---------------------------------------------------------------------------

def log_error(
    logger: logging.Logger,
    exc: BaseException,
    message: str = "",
    *,
    level: int = logging.ERROR,
    **context: Any,
) -> None:
    """Log ``exc`` as a structured error record with full traceback.

    This is the single, sanctioned way to report a contained error. It emits a
    JSON record (through the configured structured logger) that always carries
    the exception type, message, traceback and any structured context, merged
    with the error's own ``context`` when it is an :class:`OmniTrackError`.

    Args:
        logger: The logger to emit on (usually the module logger).
        exc: The exception being reported.
        message: Optional human-readable framing message.
        level: Log level (default ERROR).
        **context: Extra structured fields to attach to the record.
    """
    merged: dict[str, Any] = {}
    if isinstance(exc, OmniTrackError):
        merged["error_code"] = exc.code
        merged.update(exc.context)
    merged["error_type"] = type(exc).__name__
    merged.update(context)

    text = message or str(exc) or type(exc).__name__
    logger.log(level, text, exc_info=exc, extra={"fields": merged})
