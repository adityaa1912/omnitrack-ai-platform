"""
In-memory event storage for the event layer.

A bounded, append-only ring buffer of derived events, one per stream. This is
the persistence primitive that sits between the (pure) event engine and the
backend's API/transport layers — it holds recently derived events so they can be
queried as history and replayed to live subscribers in later commits.

Design properties:
  - Bounded memory: each buffer keeps at most `capacity` records; once full,
    appending a new record evicts the oldest (FIFO ring). A stream can run for
    days without the buffer growing without bound.
  - Append-only: records are never mutated in place. Eviction of the oldest
    record is the only removal; callers only ever add.
  - Thread-safe: a single lock guards all state, so the inference thread can
    append while an API/WebSocket handler reads concurrently.
  - Pure stdlib: no project, web, or database imports. Records are plain dicts
    (e.g. the output of `Event.to_dict()`), so this module stays decoupled from
    the event value types and is testable in complete isolation.

This module performs storage only. Wiring it into the inference loop, exposing
it over REST, and pushing to live subscribers are separate concerns handled by
later commits.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Dict, Iterable, List, Optional

# Default number of records retained per stream when no capacity is given.
DEFAULT_CAPACITY = 1000

Record = Dict[str, Any]


class EventBuffer:
    """
    A bounded, thread-safe, append-only ring buffer of event records.

    Records are plain dicts supplied by the caller (typically the JSON-friendly
    output of `Event.to_dict()`). On append, each record is shallow-copied and
    stamped with a monotonically increasing `seq` so consumers have a stable,
    gap-free ordering key independent of any field inside the record. The buffer
    never mutates a caller-provided dict.

    Retention is fixed at construction via `capacity`: the buffer holds at most
    that many of the most recent records. Older records are evicted oldest-first
    once the buffer is full.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._records: deque[Record] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        # Monotonic sequence stamped onto every appended record. Counts all
        # records ever appended (including those later evicted), so it doubles
        # as a total-appended counter and a stable per-record id.
        self._seq = 0

    # -- properties ---------------------------------------------------------

    @property
    def capacity(self) -> int:
        """Maximum number of records retained (the configured retention)."""
        return self._capacity

    @property
    def total_appended(self) -> int:
        """Count of all records ever appended, including evicted ones."""
        with self._lock:
            return self._seq

    def __len__(self) -> int:
        """Number of records currently retained (0..capacity)."""
        with self._lock:
            return len(self._records)

    # -- writes -------------------------------------------------------------

    def append(self, record: Record) -> Record:
        """
        Append a single record and return the stored copy (with its `seq`).

        The input dict is shallow-copied before the `seq` is added, so the
        caller's object is left untouched. Returns the stored record so callers
        can forward exactly what was retained (e.g. to live subscribers later).
        """
        with self._lock:
            return self._append_locked(record)

    def extend(self, records: Iterable[Record]) -> List[Record]:
        """
        Append many records atomically and return the stored copies in order.

        All records are appended under a single lock acquisition so a batch
        from one frame lands contiguously and with consecutive `seq` values.
        """
        with self._lock:
            return [self._append_locked(record) for record in records]

    def _append_locked(self, record: Record) -> Record:
        """Append one record; caller must hold `self._lock`."""
        self._seq += 1
        stored = dict(record)
        stored["seq"] = self._seq
        self._records.append(stored)  # deque(maxlen) evicts oldest when full
        return stored

    # -- reads --------------------------------------------------------------

    def snapshot(self) -> List[Record]:
        """Return all retained records, oldest-first, as a detached list."""
        with self._lock:
            return list(self._records)

    def latest(
        self,
        limit: Optional[int] = None,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> List[Record]:
        """
        Return retained records newest-first, with optional filtering.

        - `event_type` / `severity`: keep only records whose corresponding
          field matches exactly (applied before `limit`).
        - `limit`: cap the result to the `limit` most recent matching records.
          `None` returns all matches; a value <= 0 returns an empty list.
        """
        with self._lock:
            records = list(self._records)

        records.reverse()  # newest-first

        if event_type is not None:
            records = [r for r in records if r.get("event_type") == event_type]
        if severity is not None:
            records = [r for r in records if r.get("severity") == severity]

        if limit is not None:
            if limit <= 0:
                return []
            records = records[:limit]

        return records

    # -- maintenance --------------------------------------------------------

    def clear(self) -> None:
        """
        Drop all retained records.

        The `seq` counter is intentionally NOT reset, so sequence ids remain
        monotonic for the lifetime of the buffer even across a clear.
        """
        with self._lock:
            self._records.clear()


class EventStore:
    """
    A registry of per-stream `EventBuffer`s.

    Holds one bounded buffer per stream id, created on demand and retained for
    the process lifetime so event history outlives an individual stream run.
    Every buffer shares the same configured `capacity`. Thread-safe: buffers can
    be created and looked up concurrently.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._buffers: Dict[str, EventBuffer] = {}
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        """Per-stream retention applied to every buffer this store creates."""
        return self._capacity

    def get_or_create(self, stream_id: str) -> EventBuffer:
        """Return the buffer for `stream_id`, creating it if absent."""
        with self._lock:
            buffer = self._buffers.get(stream_id)
            if buffer is None:
                buffer = EventBuffer(capacity=self._capacity)
                self._buffers[stream_id] = buffer
            return buffer

    def get(self, stream_id: str) -> Optional[EventBuffer]:
        """Return the buffer for `stream_id`, or None if it has none yet."""
        with self._lock:
            return self._buffers.get(stream_id)

    def stream_ids(self) -> List[str]:
        """Return the ids of all streams that currently have a buffer."""
        with self._lock:
            return list(self._buffers.keys())

    def remove(self, stream_id: str) -> None:
        """Forget a stream's buffer entirely (drops its retained history)."""
        with self._lock:
            self._buffers.pop(stream_id, None)


# ===========================================================================
# Self-test
#
# Dependency-free; uses only stdlib. Run with:  python backend/event_store.py
# Exits non-zero on failure so it can gate CI later. Kept inline (not pytest)
# because this project has no test harness configured.
# ===========================================================================


def _rec(event_type: str, severity: str, **extra: Any) -> Record:
    """Build a minimal event-like record (as Event.to_dict() would produce)."""
    record: Record = {
        "stream_id": "selftest",
        "event_type": event_type,
        "severity": severity,
        "message": f"{event_type}/{severity}",
    }
    record.update(extra)
    return record


def test_append() -> None:
    buf = EventBuffer(capacity=10)
    src = _rec("object_appeared", "info")
    stored = buf.append(src)

    assert stored["seq"] == 1, "first append should be seq 1"
    assert stored["event_type"] == "object_appeared"
    assert "seq" not in src, "append must not mutate the caller's dict"
    assert len(buf) == 1
    assert buf.total_appended == 1


def test_extend() -> None:
    buf = EventBuffer(capacity=10)
    recs = [_rec("a", "info"), _rec("b", "low"), _rec("c", "high")]
    stored = buf.extend(recs)

    assert [r["seq"] for r in stored] == [1, 2, 3], "batch seqs must be consecutive"
    assert len(buf) == 3
    assert [r["event_type"] for r in buf.snapshot()] == ["a", "b", "c"], "order preserved"
    assert all("seq" not in r for r in recs), "extend must not mutate inputs"


def test_capacity_eviction() -> None:
    buf = EventBuffer(capacity=3)
    for i in range(5):
        buf.append(_rec(f"e{i}", "info"))

    assert len(buf) == 3, "retention must cap at capacity"
    assert buf.total_appended == 5, "total_appended counts evicted records too"

    snap = buf.snapshot()
    assert [r["event_type"] for r in snap] == ["e2", "e3", "e4"], "oldest evicted first"
    assert [r["seq"] for r in snap] == [3, 4, 5], "retained seqs are the newest"


def test_latest_limit() -> None:
    buf = EventBuffer(capacity=10)
    for i in range(5):
        buf.append(_rec(f"e{i}", "info"))

    newest_first = [r["event_type"] for r in buf.latest()]
    assert newest_first == ["e4", "e3", "e2", "e1", "e0"], "latest() is newest-first"

    assert [r["event_type"] for r in buf.latest(limit=2)] == ["e4", "e3"], "limit caps results"
    assert buf.latest(limit=0) == [], "limit<=0 returns empty"
    assert len(buf.latest(limit=100)) == 5, "limit beyond size returns all"
    assert buf.latest(limit=None) is not None and len(buf.latest()) == 5


def test_severity_filter() -> None:
    buf = EventBuffer(capacity=10)
    buf.append(_rec("a", "info"))
    buf.append(_rec("b", "critical"))
    buf.append(_rec("c", "info"))
    buf.append(_rec("d", "critical"))

    crit = buf.latest(severity="critical")
    assert [r["event_type"] for r in crit] == ["d", "b"], "filter keeps only matches, newest-first"
    assert buf.latest(severity="nope") == [], "unknown severity matches nothing"


def test_event_type_filter() -> None:
    buf = EventBuffer(capacity=10)
    buf.append(_rec("object_appeared", "info"))
    buf.append(_rec("near_collision", "critical"))
    buf.append(_rec("object_appeared", "info"))

    appeared = buf.latest(event_type="object_appeared")
    assert len(appeared) == 2, "event_type filter keeps only matches"
    assert all(r["event_type"] == "object_appeared" for r in appeared)

    # Combined filters are ANDed.
    combo = buf.latest(event_type="object_appeared", severity="critical")
    assert combo == [], "combined filters are intersected"


def test_snapshot_ordering() -> None:
    buf = EventBuffer(capacity=10)
    for i in range(4):
        buf.append(_rec(f"e{i}", "info"))

    snap = buf.snapshot()  # oldest-first
    latest = buf.latest()  # newest-first
    assert [r["event_type"] for r in snap] == ["e0", "e1", "e2", "e3"]
    assert [r["seq"] for r in snap] == [1, 2, 3, 4], "snapshot seqs ascend"
    assert list(reversed(snap)) == latest, "latest() is the reverse of snapshot()"


def test_clear() -> None:
    buf = EventBuffer(capacity=10)
    buf.extend([_rec("a", "info"), _rec("b", "info"), _rec("c", "info")])
    assert len(buf) == 3

    buf.clear()
    assert len(buf) == 0, "clear empties retained records"
    assert buf.snapshot() == []
    assert buf.total_appended == 3, "clear must NOT reset the seq counter"

    nxt = buf.append(_rec("d", "info"))
    assert nxt["seq"] == 4, "seq stays monotonic across clear"


def test_monotonic_seq() -> None:
    # Sequential: mixing append/extend yields a gap-free 1..N sequence.
    buf = EventBuffer(capacity=100)
    buf.append(_rec("a", "info"))
    buf.extend([_rec("b", "info"), _rec("c", "info")])
    buf.append(_rec("d", "info"))
    assert [r["seq"] for r in buf.snapshot()] == [1, 2, 3, 4]

    # Concurrent: many threads appending must still produce unique, gap-free
    # seqs and a correct total (proves the lock serializes seq assignment).
    threads_count, per_thread = 8, 250
    total = threads_count * per_thread
    cbuf = EventBuffer(capacity=total)  # large enough to retain everything

    def worker() -> None:
        for _ in range(per_thread):
            cbuf.append(_rec("x", "info"))

    threads = [threading.Thread(target=worker) for _ in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert cbuf.total_appended == total, "every concurrent append must be counted"
    assert len(cbuf) == total, "no records lost under concurrency"
    seqs = sorted(r["seq"] for r in cbuf.snapshot())
    assert seqs == list(range(1, total + 1)), "seqs are unique and gap-free under concurrency"


def test_store_registry() -> None:
    store = EventStore(capacity=5)
    a1 = store.get_or_create("s1")
    a2 = store.get_or_create("s1")
    assert a1 is a2, "get_or_create returns the same buffer per stream"
    assert a1.capacity == 5, "buffers inherit the store's capacity"

    assert store.get("s2") is None, "get does not create"
    store.get_or_create("s2")
    assert sorted(store.stream_ids()) == ["s1", "s2"]

    store.remove("s1")
    assert store.get("s1") is None and store.stream_ids() == ["s2"]


def _validation_checks() -> None:
    """Constructor and edge-case guards."""
    for bad in (0, -1):
        try:
            EventBuffer(capacity=bad)
        except ValueError:
            pass
        else:  # pragma: no cover - defensive
            raise AssertionError(f"EventBuffer(capacity={bad}) should raise ValueError")
    try:
        EventStore(capacity=0)
    except ValueError:
        pass
    else:  # pragma: no cover - defensive
        raise AssertionError("EventStore(capacity=0) should raise ValueError")


def _run_tests() -> int:
    tests = [
        ("append() stores a stamped copy", test_append),
        ("extend() appends a batch atomically", test_extend),
        ("capacity eviction (oldest-first)", test_capacity_eviction),
        ("latest(limit) newest-first + caps", test_latest_limit),
        ("severity filter", test_severity_filter),
        ("event_type filter", test_event_type_filter),
        ("snapshot ordering (oldest-first)", test_snapshot_ordering),
        ("clear() empties but keeps seq", test_clear),
        ("monotonic seq (sequential + concurrent)", test_monotonic_seq),
        ("EventStore registry", test_store_registry),
        ("constructor validation", _validation_checks),
    ]

    print("=" * 64)
    print("EventStore / EventBuffer self-test")
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
