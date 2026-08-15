from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from backend.settings import Settings
from backend.observability import metrics
from inference.frame_pool import (
    FramePool,
    configure_frame_pool,
    get_frame_pool,
    reset_frame_pool,
    shutdown_frame_pool,
)


@pytest.fixture(autouse=True)
def _clean_global():
    reset_frame_pool()
    yield
    reset_frame_pool()


def test_settings_accept_frame_pool_knobs() -> None:
    s = Settings(
        _env_file=None,
        frame_pool_enabled=True,
        frame_pool_initial_size=6,
        frame_pool_max_size=48,
    )
    assert s.frame_pool_enabled is True
    assert s.frame_pool_initial_size == 6
    assert s.frame_pool_max_size == 48


def test_settings_default_frame_pool_disabled() -> None:
    s = Settings(_env_file=None)
    assert s.frame_pool_enabled is False


def test_settings_reject_zero_max_size() -> None:
    with pytest.raises(Exception):
        Settings(_env_file=None, frame_pool_max_size=0)


def test_settings_reject_negative_initial_size() -> None:
    with pytest.raises(Exception):
        Settings(_env_file=None, frame_pool_initial_size=-1)


def test_disabled_pool_never_retains_and_allocates_each_time() -> None:
    pool = FramePool(enabled=False)
    a = pool.acquire((4, 4, 3), np.uint8)
    pool.release(a)
    b = pool.acquire((4, 4, 3), np.uint8)
    assert b is not a
    stats = pool.stats()
    assert stats["size"] == 0
    assert stats["hits"] == 0
    assert stats["misses"] == 2


def test_enabled_pool_reuses_released_buffer() -> None:
    pool = FramePool(enabled=True, initial_size=0, max_size=8)
    a = pool.acquire((8, 8, 3), np.uint8)
    pool.release(a)
    b = pool.acquire((8, 8, 3), np.uint8)
    assert b is a
    assert pool.stats()["hits"] == 1
    assert pool.stats()["misses"] == 1


def test_shape_dtype_isolation() -> None:
    pool = FramePool(enabled=True, max_size=8)
    a = pool.acquire((4, 4, 3), np.uint8)
    pool.release(a)
    other = pool.acquire((4, 4, 3), np.float32)
    assert other is not a
    same = pool.acquire((4, 4, 3), np.uint8)
    assert same is a


def test_initial_size_seeds_spares_on_first_use() -> None:
    pool = FramePool(enabled=True, initial_size=3, max_size=16)
    first = pool.acquire((2, 2, 3), np.uint8)
    assert pool.stats()["size"] == 3
    seeded = [pool.acquire((2, 2, 3), np.uint8) for _ in range(3)]
    assert pool.stats()["size"] == 0
    assert pool.stats()["hits"] == 3
    assert all(first is not buf for buf in seeded)


def test_max_size_caps_retained_buffers() -> None:
    pool = FramePool(enabled=True, initial_size=0, max_size=2)
    buffers = [pool.acquire((3, 3, 3), np.uint8) for _ in range(4)]
    for buf in buffers:
        pool.release(buf)
    assert pool.stats()["size"] == 2


def test_copy_of_produces_exact_independent_copy() -> None:
    pool = FramePool(enabled=True, max_size=4)
    source = np.arange(2 * 2 * 3, dtype=np.uint8).reshape(2, 2, 3)
    copy = pool.copy_of(source)
    assert np.array_equal(copy, source)
    assert copy is not source
    copy[0, 0, 0] = 200
    assert source[0, 0, 0] == 0
    assert pool.stats()["copies"] == 1


def test_borrow_releases_on_exit() -> None:
    pool = FramePool(enabled=True, max_size=4)
    with pool.borrow((5, 5, 3), np.uint8) as buf:
        assert buf.shape == (5, 5, 3)
    assert pool.stats()["size"] == 1
    again = pool.acquire((5, 5, 3), np.uint8)
    assert again is buf


def test_reuse_ratio_reflects_hit_fraction() -> None:
    pool = FramePool(enabled=True, max_size=4)
    a = pool.acquire((2, 2, 3), np.uint8)
    pool.release(a)
    pool.acquire((2, 2, 3), np.uint8)
    assert pool.stats()["reuse_ratio"] == pytest.approx(0.5)


def test_cleanup_drops_idle_buffers() -> None:
    pool = FramePool(enabled=True, max_size=4, idle_seconds=10.0)
    pool.release(pool.acquire((2, 2, 3), np.uint8))
    assert pool.stats()["size"] == 1
    assert pool.cleanup(idle_seconds=1000.0) == 0
    assert pool.stats()["size"] == 1
    time.sleep(0.05)
    dropped = pool.cleanup(idle_seconds=0.01)
    assert dropped == 1
    assert pool.stats()["size"] == 0


def test_shutdown_clears_pool() -> None:
    pool = FramePool(enabled=True, max_size=4)
    pool.release(pool.acquire((2, 2, 3), np.uint8))
    pool.shutdown()
    assert pool.stats()["size"] == 0


def test_global_singleton_configure_and_reset() -> None:
    configured = configure_frame_pool(enabled=True, initial_size=2, max_size=10)
    assert get_frame_pool() is configured
    assert get_frame_pool().enabled is True
    shutdown_frame_pool()
    fresh = get_frame_pool()
    assert fresh is not configured
    assert fresh.enabled is False


def test_get_frame_pool_lazily_creates_disabled_default() -> None:
    reset_frame_pool()
    pool = get_frame_pool()
    assert pool.enabled is False


def test_frame_pool_metrics_exposed() -> None:
    configure_frame_pool(enabled=True, initial_size=0, max_size=8)
    pool = get_frame_pool()
    a = pool.acquire((4, 4, 3), np.uint8)
    pool.release(a)
    pool.acquire((4, 4, 3), np.uint8)
    pool.copy_of(np.zeros((4, 4, 3), dtype=np.uint8))

    text = metrics.render_metrics().decode("utf-8")
    assert "omnitrack_frame_pool_size" in text
    assert "omnitrack_buffer_reuse_ratio" in text
    assert "omnitrack_frame_pool_hits_total" in text
    assert "omnitrack_frame_pool_misses_total" in text
    assert "omnitrack_frame_allocations_total" in text
    assert "omnitrack_copy_operations_total" in text


def test_concurrent_acquire_hands_out_distinct_buffers() -> None:
    pool = FramePool(enabled=True, initial_size=0, max_size=64)
    seen: list = []
    lock = threading.Lock()
    acquired = threading.Barrier(8)
    may_release = threading.Barrier(8)

    def worker():
        acquired.wait()
        buf = pool.acquire((16, 16, 3), np.uint8)
        buf[:] = 0
        with lock:
            seen.append(id(buf))
        may_release.wait()
        pool.release(buf)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(seen)) == 8
