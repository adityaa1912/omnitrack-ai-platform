"""
Correlation-ID propagation for the OmniTrack platform.

A *correlation id* (here ``request_id``) ties together every log record and
metric produced while handling one logical unit of work — an HTTP request, a
WebSocket connection, or one inference-loop iteration — so a single operation
can be traced across threads and subsystems.

Propagation model
-----------------
The active id is stored in a :class:`contextvars.ContextVar`. ``ContextVar`` is
the correct primitive here because it is:

* **async-safe** — each ``asyncio`` task sees its own value, so concurrent
  requests on one event loop never leak ids into each other.
* **thread-safe** — each thread gets an independent context, so an inference
  thread can pin a stream-scoped id without disturbing the request loop.

For the inference pipeline (which runs on its own thread, decoupled from any
single request) we propagate the *stream* identity rather than a request id:
the stream's correlation context is established once per loop iteration via
:func:`bind_stream_context`.

The module is pure stdlib and importable from any layer (backend, inference)
without pulling in web or logging dependencies.
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Optional

# ---------------------------------------------------------------------------
# Context variables. ``None`` means "no id bound in this context yet"; the
# logging formatter substitutes "-" for absent values so records stay
# schema-complete even on code paths that never bind an id.
# ---------------------------------------------------------------------------

_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "omnitrack_request_id", default=None
)
_stream_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "omnitrack_stream_id", default=None
)
_camera_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "omnitrack_camera_id", default=None
)
_event_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "omnitrack_event_id", default=None
)


def new_request_id() -> str:
    """Return a fresh, globally-unique request id (UUID4 hex, no dashes)."""
    return uuid.uuid4().hex


def new_event_id() -> str:
    """Return a fresh, globally-unique event id."""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Request id
# ---------------------------------------------------------------------------

def bind_request_id(request_id: Optional[str] = None) -> str:
    """Bind ``request_id`` (or a freshly generated one) to the current context.

    Returns the id that was bound, so callers can echo it back to clients.
    """
    rid = request_id or new_request_id()
    _request_id.set(rid)
    return rid


def get_request_id() -> Optional[str]:
    """Return the request id bound to the current context, if any."""
    return _request_id.get()


def clear_request_id() -> None:
    """Remove any request id from the current context."""
    _request_id.set(None)


# ---------------------------------------------------------------------------
# Stream / camera / event context
# ---------------------------------------------------------------------------

def bind_stream_context(
    stream_id: Optional[str] = None,
    camera_id: Optional[str] = None,
) -> None:
    """Pin stream (and future camera) identity to the current context.

    Called once per inference-loop iteration (and at WebSocket accept) so every
    downstream log record carries the stream it belongs to. ``camera_id`` is a
    future-ready field: it is accepted and propagated now but may be ``None``
    until camera topology is introduced.
    """
    if stream_id is not None:
        _stream_id.set(stream_id)
    if camera_id is not None:
        _camera_id.set(camera_id)


def bind_event_id(event_id: Optional[str] = None) -> str:
    """Bind an event id to the current context, returning the bound id."""
    eid = event_id or new_event_id()
    _event_id.set(eid)
    return eid


def get_stream_id() -> Optional[str]:
    return _stream_id.get()


def get_camera_id() -> Optional[str]:
    return _camera_id.get()


def get_event_id() -> Optional[str]:
    return _event_id.get()


def clear_stream_context() -> None:
    """Remove stream/camera/event identity from the current context."""
    _stream_id.set(None)
    _camera_id.set(None)
    _event_id.set(None)


_user_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "omnitrack_user_id", default=None
)


def bind_user_id(user_id) -> None:
    _user_id.set(str(user_id) if user_id is not None else None)


def get_user_id() -> Optional[str]:
    return _user_id.get()


def clear_user_id() -> None:
    _user_id.set(None)
