"""
Targeted self-test for the event-history REST API (Commit 3C).

Run with:  python -m backend._events_api_selftest

Importing this module imports `backend.main`, which is itself the import-check:
it constructs the FastAPI app and the InferenceService. The tests then:
  - assert the new GET /stream/{stream_id}/events route is registered,
  - assert the existing routes are still present (regression guard),
  - drive the endpoint handler directly (no HTTP server, no httpx) against the
    in-memory event store and check ordering, limit, seq preservation, the
    empty-history case, and the typed response shape.

Kept as a runnable module (not pytest) to match this project's test style; it
exits non-zero on failure so it can gate CI later. The endpoint reads the event
store only — no database is touched by the code under test.
"""

from __future__ import annotations

import asyncio

from .main import (
    DEFAULT_EVENT_LIMIT,
    MAX_EVENT_LIMIT,
    EventResponse,
    app,
    get_events,
    service,
)
from inference.events import EventType, Severity
from inference.events.event_types import Event


# --- helpers ---------------------------------------------------------------


def _http_routes() -> set[tuple[str, str]]:
    """All (path, method) pairs for HTTP routes registered on the app."""
    pairs: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if methods and path:
            for method in methods:
                pairs.add((path, method))
    return pairs


def _websocket_paths() -> set[str]:
    """Paths of WebSocket routes (which carry no HTTP `methods`)."""
    return {
        getattr(route, "path", None)
        for route in app.routes
        if route.__class__.__name__ in ("WebSocketRoute", "APIWebSocketRoute")
    }


def _seed(stream_id: str, count: int) -> list[Event]:
    """Append `count` synthetic events to a stream's buffer; return them."""
    buffer = service.event_store.get_or_create(stream_id)
    events = [
        Event(
            stream_id=stream_id,
            event_type=EventType.OBJECT_APPEARED,
            severity=Severity.INFO,
            frame_id=i,
            timestamp=i / 30.0,
            track_id=i,
            class_name="person",
            message=f"event {i}",
            metadata={"i": i},
        )
        for i in range(count)
    ]
    buffer.extend(e.to_dict() for e in events)
    return events


def _call(stream_id: str, limit: int) -> list[EventResponse]:
    """Invoke the async endpoint handler directly and return its result."""
    return asyncio.run(get_events(stream_id, limit=limit))


# --- tests -----------------------------------------------------------------


def test_new_route_registered() -> None:
    assert ("/stream/{stream_id}/events", "GET") in _http_routes(), (
        "GET /stream/{stream_id}/events must be registered"
    )


def test_existing_routes_present() -> None:
    """Regression guard: the routes that existed before 3C still exist."""
    routes = _http_routes()
    expected = {
        ("/health", "GET"),
        ("/stream/start", "POST"),
        ("/stream/stop", "POST"),
        ("/stream/{stream_id}/metrics", "GET"),
        ("/stream/{stream_id}/detections", "GET"),
        ("/streams", "GET"),
        ("/", "GET"),
    }
    missing = expected - routes
    assert not missing, f"existing routes disappeared: {sorted(missing)}"
    assert "/stream/{stream_id}/ws" in _websocket_paths(), (
        "frame WebSocket route must still be registered"
    )


def test_limit_bounds_sane() -> None:
    assert 1 <= DEFAULT_EVENT_LIMIT <= MAX_EVENT_LIMIT, "default must be within bounds"
    assert MAX_EVENT_LIMIT <= service.event_store.capacity or MAX_EVENT_LIMIT >= 1, (
        "max limit must be a positive bound"
    )


def test_empty_history_returns_empty() -> None:
    result = _call("nonexistent-stream-3c", limit=DEFAULT_EVENT_LIMIT)
    assert result == [], "streams with no history must yield an empty list"


def test_newest_first_and_seq_preserved() -> None:
    stream_id = "selftest-3c-order"
    _seed(stream_id, 5)
    result = _call(stream_id, limit=100)

    assert len(result) == 5, "all retained events returned when under limit"
    assert all(isinstance(r, EventResponse) for r in result), "typed response model"

    # Newest-first: frame_id descends 4..0, seq descends and is gap-free.
    assert [r.frame_id for r in result] == [4, 3, 2, 1, 0], "newest-first ordering"
    seqs = [r.seq for r in result]
    assert seqs == sorted(seqs, reverse=True), "seq is newest-first"
    assert seqs == [5, 4, 3, 2, 1], "seq values preserved from the buffer"


def test_limit_caps_results() -> None:
    stream_id = "selftest-3c-limit"
    _seed(stream_id, 8)
    result = _call(stream_id, limit=3)

    assert len(result) == 3, "limit caps the number of returned events"
    assert [r.frame_id for r in result] == [7, 6, 5], "the newest `limit` events"


def test_response_shape() -> None:
    stream_id = "selftest-3c-shape"
    _seed(stream_id, 1)
    [event] = _call(stream_id, limit=1)

    assert event.stream_id == stream_id
    assert event.event_type == "object_appeared"
    assert event.severity == "info"
    assert event.severity_rank == 0
    assert event.track_id == 0
    assert event.class_name == "person"
    assert event.metadata == {"i": 0}
    # All declared fields present and JSON-serializable via the model.
    dumped = event.model_dump()
    assert set(dumped) == {
        "seq", "stream_id", "event_type", "severity", "severity_rank",
        "frame_id", "timestamp", "track_id", "class_name", "message", "metadata",
    }, "response carries exactly the declared event fields"


def _run_tests() -> int:
    tests = [
        ("new events route registered", test_new_route_registered),
        ("existing routes still present (regression)", test_existing_routes_present),
        ("limit bounds are sane", test_limit_bounds_sane),
        ("empty history returns empty list", test_empty_history_returns_empty),
        ("newest-first ordering + seq preserved", test_newest_first_and_seq_preserved),
        ("limit caps results", test_limit_caps_results),
        ("typed response shape", test_response_shape),
    ]

    print("=" * 64)
    print("Event history REST API self-test")
    print("=" * 64)

    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  [PASS] {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  [FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface any unexpected error
            failures += 1
            print(f"  [ERROR] {name}: {exc!r}")

    print("-" * 64)
    print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    import sys

    sys.exit(_run_tests())
