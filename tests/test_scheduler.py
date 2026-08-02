from __future__ import annotations

import threading
import time

import pytest

from backend.settings import Settings
from backend.scheduler import InferenceScheduler
from backend.observability import metrics


class _FakeSessionFactory:
    """Minimal scoped_session stand-in: callable + ``remove`` counter."""

    def __init__(self) -> None:
        self.created = 0
        self.removed = 0
        self._lock = threading.Lock()

    def __call__(self) -> "_FakeSession":
        with self._lock:
            self.created += 1
        return _FakeSession()

    def remove(self) -> None:
        with self._lock:
            self.removed += 1


class _FakeSession:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _drops(stream_id: str) -> float:
    return metrics.DROPPED_FRAMES_TOTAL.labels(stream_id=stream_id)._value.get()


def test_settings_accept_scheduler_knobs() -> None:
    s = Settings(
        _env_file=None,
        scheduler_enabled=True,
        scheduler_workers=3,
        scheduler_stream_queue_capacity=4,
    )
    assert s.scheduler_enabled is True
    assert s.scheduler_workers == 3
    assert s.scheduler_stream_queue_capacity == 4


def test_settings_reject_zero_workers() -> None:
    with pytest.raises(Exception):
        Settings(_env_file=None, scheduler_workers=0)


def test_frames_reach_process_fn() -> None:
    factory = _FakeSessionFactory()
    scheduler = InferenceScheduler(factory, num_workers=1, stream_queue_capacity=4)
    seen: list = []
    try:
        scheduler.register("reach", lambda frame, db: seen.append(frame))
        scheduler.submit("reach", "f1")
        assert _wait_until(lambda: seen == ["f1"])
    finally:
        scheduler.stop()


def test_newest_frame_priority_under_overload() -> None:
    factory = _FakeSessionFactory()
    scheduler = InferenceScheduler(factory, num_workers=1, stream_queue_capacity=2)
    seen: list = []
    picked_up = threading.Event()
    release = threading.Event()

    def process_fn(frame, db) -> None:
        seen.append(frame)
        picked_up.set()
        release.wait(timeout=5.0)

    before = _drops("burst")
    try:
        scheduler.register("burst", process_fn)
        scheduler.submit("burst", 1)
        assert picked_up.wait(timeout=2.0)  # worker is now blocked on frame 1

        # Burst while the worker is busy: capacity 2 drops the oldest on the
        # 4th/5th submit, and the worker later takes only the newest (5),
        # dropping the remaining queued frame.
        for frame_id in (2, 3, 4, 5):
            scheduler.submit("burst", frame_id)
        release.set()

        assert _wait_until(lambda: seen == [1, 5])
        assert _drops("burst") - before == 3
        assert (
            metrics.SCHEDULER_QUEUE_DEPTH.labels(stream_id="burst")._value.get() == 0
        )
    finally:
        release.set()
        scheduler.stop()


def test_slow_stream_does_not_block_other_streams() -> None:
    factory = _FakeSessionFactory()
    scheduler = InferenceScheduler(factory, num_workers=2, stream_queue_capacity=2)
    blocked = threading.Event()
    release = threading.Event()
    fast_seen: list = []

    def slow(frame, db) -> None:
        blocked.set()
        release.wait(timeout=5.0)

    try:
        scheduler.register("slow", slow)
        scheduler.register("fast", lambda frame, db: fast_seen.append(frame))
        scheduler.submit("slow", "S1")
        assert blocked.wait(timeout=2.0)  # one worker is stuck on the slow stream

        scheduler.submit("fast", "F1")
        assert _wait_until(lambda: fast_seen == ["F1"])
    finally:
        release.set()
        scheduler.stop()


def test_process_fn_error_terminates_owner_stream() -> None:
    factory = _FakeSessionFactory()
    scheduler = InferenceScheduler(factory, num_workers=1, stream_queue_capacity=2)

    class _Owner:
        def __init__(self) -> None:
            self.is_running = True
            self.metrics = type("M", (), {"is_active": True, "error_message": None})()

    owner = _Owner()

    def boom(frame, db) -> None:
        raise RuntimeError("device fault")

    try:
        scheduler.register("boom", boom, owner=owner)
        scheduler.submit("boom", 1)
        assert _wait_until(lambda: owner.is_running is False)
        assert owner.metrics.is_active is False
        assert owner.metrics.error_message == "device fault"
    finally:
        scheduler.stop()


def test_stop_joins_workers_and_releases_sessions() -> None:
    factory = _FakeSessionFactory()
    scheduler = InferenceScheduler(factory, num_workers=2, stream_queue_capacity=2)
    scheduler.register("s", lambda frame, db: None)
    scheduler.submit("s", 1)

    scheduler.stop()

    assert scheduler._stopping is True
    assert all(not t.is_alive() for t in scheduler._workers)
    # Each worker created and released exactly one session.
    assert factory.created == 2
    assert factory.removed == 2
