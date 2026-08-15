"""
Process-wide frame pool: a shared, thread-safe cache of reusable image buffers.

Every pipeline stage that would otherwise allocate a fresh ``numpy`` array per
frame (render output, letterbox canvas, resize destination, inference tensor)
can instead borrow a right-shaped buffer from this pool and return it when done,
so steady-state operation reuses a small working set instead of churning the
allocator. The pool is keyed by ``(shape, dtype)`` so heterogeneous buffers
never collide, grows on demand up to a configurable cap, seeds an initial batch
of spares on first use of a shape, and reclaims buffers left idle too long.

Design notes / invariants:
* Disabled by default. A pool with ``enabled=False`` never retains a released
  buffer, so every ``acquire`` allocates exactly as the un-pooled code did and
  behavior is byte-for-byte unchanged. Reuse only happens when explicitly
  enabled.
* Handout ownership. ``acquire`` removes a buffer from the pool; only an
  explicit ``release`` returns it. A buffer that is never released is simply
  garbage-collected, so a missed release costs reuse, never correctness.
* Thread-safe. All pool state is guarded by a single lock; the pool never calls
  back into callers, so concurrent stream threads sharing one detector each get
  a distinct buffer with no lock-ordering hazard.
* Bounded memory. At most ``max_size`` buffers are retained across all shapes;
  buffers beyond the cap or idle past the cleanup interval are dropped.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np


_DEFAULT_MAX_SIZE = 32
_DEFAULT_IDLE_SECONDS = 30.0


class FramePool:
    """Thread-safe pool of reusable ``numpy`` image buffers keyed by shape/dtype."""

    def __init__(
        self,
        enabled: bool = False,
        initial_size: int = 0,
        max_size: int = _DEFAULT_MAX_SIZE,
        idle_seconds: float = _DEFAULT_IDLE_SECONDS,
    ) -> None:
        self.enabled = bool(enabled)
        self._initial_size = max(int(initial_size), 0)
        self._max_size = max(int(max_size), 1)
        self._idle_seconds = max(float(idle_seconds), 0.0)
        self._free: "Dict[Tuple, List[Tuple[np.ndarray, float]]]" = {}
        self._seeded: set = set()
        self._free_count = 0
        self._next_cleanup_at = 0.0
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._allocations = 0
        self._copies = 0

    @staticmethod
    def _key(shape, dtype) -> Tuple:
        return (tuple(shape), np.dtype(dtype).str)

    def acquire(self, shape, dtype=np.uint8) -> np.ndarray:
        """Return a buffer of the given shape/dtype, reused when one is free."""
        key = self._key(shape, dtype)
        with self._lock:
            bucket = self._free.get(key)
            if bucket:
                array, _ = bucket.pop()
                self._free_count -= 1
                self._hits += 1
                return array
            self._misses += 1
            if self.enabled and key not in self._seeded:
                self._seeded.add(key)
                seed = min(self._initial_size, max(0, self._max_size - self._free_count))
                if seed > 0:
                    now = time.monotonic()
                    spare = self._free.setdefault(key, [])
                    for _ in range(seed):
                        spare.append((np.empty(shape, dtype), now))
                        self._allocations += 1
                        self._free_count += 1
            self._allocations += 1
            return np.empty(shape, dtype)

    def release(self, array: Optional[np.ndarray]) -> None:
        """Return a buffer to the pool for reuse (no-op when disabled)."""
        if array is None or not self.enabled:
            return
        now = time.monotonic()
        key = self._key(array.shape, array.dtype)
        with self._lock:
            if self._free_count < self._max_size:
                self._free.setdefault(key, []).append((array, now))
                self._free_count += 1
            if now >= self._next_cleanup_at:
                self._next_cleanup_at = now + self._idle_seconds
                self._cleanup_locked(now - self._idle_seconds)

    def copy_of(self, source: np.ndarray) -> np.ndarray:
        """Return a pooled buffer holding an exact copy of ``source``."""
        target = self.acquire(source.shape, source.dtype)
        np.copyto(target, source)
        with self._lock:
            self._copies += 1
        return target

    @contextmanager
    def borrow(self, shape, dtype=np.uint8) -> Iterator[np.ndarray]:
        """Borrow a buffer for the duration of a ``with`` block, then release it."""
        array = self.acquire(shape, dtype)
        try:
            yield array
        finally:
            self.release(array)

    def cleanup(self, idle_seconds: Optional[float] = None) -> int:
        """Drop free buffers idle longer than the threshold; return the count."""
        idle = self._idle_seconds if idle_seconds is None else max(float(idle_seconds), 0.0)
        with self._lock:
            return self._cleanup_locked(time.monotonic() - idle)

    def _cleanup_locked(self, cutoff: float) -> int:
        dropped = 0
        for key in list(self._free.keys()):
            bucket = self._free[key]
            kept = [(a, t) for (a, t) in bucket if t >= cutoff]
            dropped += len(bucket) - len(kept)
            if kept:
                self._free[key] = kept
            else:
                del self._free[key]
                self._seeded.discard(key)
        self._free_count -= dropped
        return dropped

    def shutdown(self) -> None:
        """Release every retained buffer and empty the pool (graceful cleanup)."""
        with self._lock:
            self._free.clear()
            self._seeded.clear()
            self._free_count = 0

    def stats(self) -> Dict[str, float]:
        """Snapshot of pool occupancy and reuse counters for introspection."""
        with self._lock:
            total = self._hits + self._misses
            reuse_ratio = (self._hits / total) if total else 0.0
            return {
                "enabled": self.enabled,
                "size": self._free_count,
                "hits": self._hits,
                "misses": self._misses,
                "allocations": self._allocations,
                "copies": self._copies,
                "reuse_ratio": reuse_ratio,
                "max_size": self._max_size,
                "initial_size": self._initial_size,
            }


_global_lock = threading.Lock()
_global_pool: Optional[FramePool] = None


def configure_frame_pool(
    enabled: bool = False,
    initial_size: int = 0,
    max_size: int = _DEFAULT_MAX_SIZE,
    idle_seconds: float = _DEFAULT_IDLE_SECONDS,
) -> FramePool:
    """Build the process-wide frame pool and install it as the global instance."""
    global _global_pool
    pool = FramePool(
        enabled=enabled,
        initial_size=initial_size,
        max_size=max_size,
        idle_seconds=idle_seconds,
    )
    with _global_lock:
        _global_pool = pool
    return pool


def get_frame_pool() -> FramePool:
    """Return the process-wide frame pool, creating a disabled one on first use."""
    global _global_pool
    with _global_lock:
        if _global_pool is None:
            _global_pool = FramePool()
        return _global_pool


def shutdown_frame_pool() -> None:
    """Release the process-wide pool's buffers and drop it (service shutdown)."""
    global _global_pool
    with _global_lock:
        pool = _global_pool
        _global_pool = None
    if pool is not None:
        pool.shutdown()


def reset_frame_pool() -> None:
    """Drop the process-wide pool reference without cleanup (test helper)."""
    global _global_pool
    with _global_lock:
        _global_pool = None
