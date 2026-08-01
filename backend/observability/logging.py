"""
Structured JSON logging for the OmniTrack platform.

Every log record emitted anywhere in the backend or inference pipeline is
serialized as a single JSON object on one line, with a stable, schema-complete
set of fields. This makes logs trivially parseable by Loki / ELK / CloudWatch
and greppable by humans.

Guaranteed fields (always present, ``null``/``-`` when not applicable):

    timestamp   ISO-8601 UTC, millisecond precision
    level       record level name (INFO, ERROR, ...)
    logger      logger (module) name
    component   logical subsystem (backend.api, inference.stream, ...)
    request_id  correlation id for the active request, else "-"
    stream_id   stream the record pertains to, else "-"
    camera_id   future-ready camera identity, else "-"
    event_id    derived-event id, else "-"
    thread      thread name + native id
    message     the human-readable message
    exception   formatted traceback string when present, else null

Design properties
-----------------
* **Non-blocking** — records are formatted and emitted by a
  :class:`logging.handlers.QueueListener` draining a :class:`queue.Queue` on a
  dedicated daemon thread. The calling thread only ever does an unbounded,
  non-blocking ``put_nowait``; logging can never block the inference hot path
  or the ASGI loop.
* **Thread-safe** — the stdlib logging queue machinery is already
  thread-safe; we only add a JSON formatter.
* **Low overhead** — formatting happens off the caller thread; the caller
  pays only for building the record and a queue put.
* **Zero API change** — this only *configures* logging; it does not change
  any public interface.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import queue
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from . import correlation

# Field name used to carry an explicit component override on a record.
_COMPONENT_ATTR = "component"

# The listener + queue are process-wide singletons, created once by
# ``configure_logging`` and torn down on shutdown.
_log_queue: "queue.Queue[logging.LogRecord]" = queue.Queue()
_listener: Optional[logging.handlers.QueueListener] = None
_configured = False
_config_lock = threading.Lock()


def _iso_utc(ts: float) -> str:
    """Format a POSIX timestamp as ISO-8601 UTC with millisecond precision."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonFormatter(logging.Formatter):
    """Serialize a :class:`logging.LogRecord` to a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D102
        # ``record.getMessage()`` applies %-formatting of args lazily, here on
        # the listener thread rather than the caller thread.
        message = record.getMessage()

        exception: Optional[str] = None
        if record.exc_info:
            exception = self.formatException(record.exc_info)
        elif record.exc_text:
            exception = record.exc_text

        payload: dict[str, Any] = {
            "timestamp": _iso_utc(record.created),
            "level": record.levelname,
            "logger": record.name,
            # Component: explicit per-record override wins, else derive from the
            # logger name (e.g. "backend.service" -> "backend.service").
            "component": getattr(record, _COMPONENT_ATTR, None) or record.name,
            "request_id": correlation.get_request_id() or "-",
            "stream_id": correlation.get_stream_id() or "-",
            "camera_id": correlation.get_camera_id() or "-",
            "event_id": correlation.get_event_id() or "-",
            "thread": f"{record.threadName}:{record.thread}",
            "message": message,
            "exception": exception,
        }

        # Merge any structured extras the caller attached via
        # ``logger.info("...", extra={"fields": {...}})`` so callers can add
        # arbitrary, queryable context without changing the schema.
        extra_fields = getattr(record, "fields", None)
        if isinstance(extra_fields, dict):
            for key, value in extra_fields.items():
                # Never let an extra clobber the guaranteed schema keys.
                if key not in payload:
                    payload[key] = value

        return json.dumps(payload, default=str, ensure_ascii=False)


class _ComponentAdapter(logging.LoggerAdapter):
    """Logger adapter that stamps a stable ``component`` on every record.

    Lets a subsystem pin a human-meaningful component name once instead of
    relying on the dotted module path, e.g. ``get_logger(__name__, "api")``.
    """

    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:  # noqa: D102
        extra = kwargs.setdefault("extra", {})
        extra.setdefault(_COMPONENT_ATTR, self.extra.get(_COMPONENT_ATTR))
        return msg, kwargs


def get_logger(name: str, component: Optional[str] = None) -> logging.Logger:
    """Return a standard logger; optionally pin a stable component name.

    This is a thin convenience over :func:`logging.getLogger`. When
    ``component`` is given, an adapter is returned that injects it into every
    record; otherwise the plain logger is returned (component falls back to the
    logger name). Either result is a drop-in ``logging.Logger``-compatible
    object, so existing call sites need no change.
    """
    base = logging.getLogger(name)
    if component is None:
        return base
    return _ComponentAdapter(base, {_COMPONENT_ATTR: component})  # type: ignore[return-value]


def configure_logging(level: str = "INFO") -> None:
    """Configure process-wide structured JSON logging (idempotent).

    Replaces the root logger's handlers with a single non-blocking
    queue-backed handler writing JSON to stdout. Safe to call more than once;
    subsequent calls are no-ops. Uvicorn's own loggers propagate to root, so
    they are captured and JSON-formatted too.
    """
    global _listener, _configured
    with _config_lock:
        if _configured:
            return

        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(JsonFormatter())

        # QueueListener owns the real handler and emits on its own thread.
        listener = logging.handlers.QueueListener(
            _log_queue, stdout_handler, respect_handler_level=True
        )

        queue_handler = logging.handlers.QueueHandler(_log_queue)

        root = logging.getLogger()
        # Drop any pre-existing handlers (e.g. basicConfig from run.py) so all
        # output flows through the single JSON queue path.
        for existing in list(root.handlers):
            root.removeHandler(existing)
        root.addHandler(queue_handler)
        root.setLevel(level.upper())

        listener.start()
        _listener = listener
        _configured = True


def shutdown_logging() -> None:
    """Flush and stop the logging listener. Call once at process shutdown.

    Ensures buffered records are written before exit so no logs are lost
    during graceful shutdown.
    """
    global _listener, _configured
    with _config_lock:
        if _listener is not None:
            try:
                _listener.stop()
            finally:
                _listener = None
        _configured = False
