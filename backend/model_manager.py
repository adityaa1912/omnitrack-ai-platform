"""
Process-wide model manager: a shared, reference-counted detector pool.

Streams that share an identical detector configuration should share one loaded
model instead of each building a private :class:`~inference.detector.Detector`.
The :class:`ModelManager` owns that pool: it lazily loads one detector per
provider-aware signature, hands the shared instance to every acquirer, and
reference-counts them so a model is only a candidate for release once no stream
holds it. A bounded pool (``max_loaded``) keeps memory in check by evicting the
least-recently-used *idle* model when a new one would exceed the cap; a model a
live stream still holds is never evicted (the pool is allowed to exceed the cap
transiently rather than pull a model out from under a running stream).

Design notes / invariants:
* Provider-aware cache key. The signature reuses
  :func:`backend.batching.detector_signature`, so two configs share a model iff
  they agree on model, device, provider, thresholds, resolution and precision.
  Heterogeneous deployments never collide.
* Lazy + warm. Loading happens on first acquire (lazy). ``warm`` preloads a
  model at refcount zero so a later acquire is a cache hit; a released model
  also stays resident (warm) until evicted, so restart churn is cheap.
* Memory-safe unload. Eviction and shutdown drop the detector's provider (and
  model) references so the underlying accelerator/runtime memory is freed once
  no acquirer holds the detector.
* Thread-safe. All pool mutations happen under a single lock; the pool never
  calls back into acquirers, so no lock-ordering hazard exists with callers.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable, Dict, List, Optional

from inference.config import DetectorConfig
from inference.detector import Detector
from .batching import detector_signature
from .observability import metrics
from .observability.logging import get_logger


logger = get_logger(__name__, component="inference.model_manager")

# Builds a detector for a config. Injectable so tests can supply a lightweight
# stand-in without loading real model weights.
DetectorFactory = Callable[[DetectorConfig], Detector]


class _Entry:
    """One pooled detector plus its live reference count."""

    __slots__ = ("detector", "signature", "refcount")

    def __init__(self, detector: Detector, signature: str) -> None:
        self.detector = detector
        self.signature = signature
        self.refcount = 0


class ModelManager:
    """Reference-counted, LRU-bounded pool of shared detectors.

    ``acquire`` returns the shared detector for a config, loading it on the
    first request (cache miss) and reusing it thereafter (cache hit). ``release``
    drops one reference; a model at zero references stays resident (warm) until
    evicted to respect ``max_loaded`` or dropped by ``shutdown``.
    """

    def __init__(self, max_loaded: int = 4, factory: DetectorFactory = Detector) -> None:
        self._max_loaded = max(int(max_loaded), 1)
        self._factory = factory
        self._entries: "OrderedDict[str, _Entry]" = OrderedDict()
        self._lock = threading.Lock()

    def acquire(self, config: DetectorConfig) -> Detector:
        """Return the shared detector for ``config``, loading it if absent."""
        signature = detector_signature(config)
        with self._lock:
            entry = self._entries.get(signature)
            if entry is not None:
                entry.refcount += 1
                self._entries.move_to_end(signature)
                metrics.MODEL_CACHE_HITS_TOTAL.inc()
                return entry.detector
            metrics.MODEL_CACHE_MISSES_TOTAL.inc()
            self._evict_locked()
            detector = self._load(signature, config)
            entry = _Entry(detector, signature)
            entry.refcount = 1
            self._entries[signature] = entry
            metrics.MODEL_LOADED_MODELS.set(len(self._entries))
            return detector

    def release(self, signature: str) -> None:
        """Drop one reference to the detector identified by ``signature``."""
        with self._lock:
            entry = self._entries.get(signature)
            if entry is None:
                return
            entry.refcount = max(entry.refcount - 1, 0)

    def warm(self, config: DetectorConfig) -> None:
        """Preload the model for ``config`` at zero references (warm cache)."""
        signature = detector_signature(config)
        with self._lock:
            if signature in self._entries:
                self._entries.move_to_end(signature)
                return
            metrics.MODEL_CACHE_MISSES_TOTAL.inc()
            self._evict_locked()
            detector = self._load(signature, config)
            self._entries[signature] = _Entry(detector, signature)
            metrics.MODEL_LOADED_MODELS.set(len(self._entries))

    def shutdown(self) -> None:
        """Unload every resident model and empty the pool (graceful cleanup)."""
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
            metrics.MODEL_LOADED_MODELS.set(0)
        for entry in entries:
            self._unload(entry)

    def loaded_count(self) -> int:
        """Number of models currently resident in the pool."""
        with self._lock:
            return len(self._entries)

    def signatures(self) -> List[str]:
        """Resident model signatures, least-recently-used first."""
        with self._lock:
            return list(self._entries.keys())

    def stats(self) -> Dict[str, int]:
        """Resident, in-use and cap counts for readiness/introspection."""
        with self._lock:
            in_use = sum(1 for entry in self._entries.values() if entry.refcount > 0)
            return {
                "loaded": len(self._entries),
                "in_use": in_use,
                "max_loaded": self._max_loaded,
            }

    @property
    def max_loaded(self) -> int:
        return self._max_loaded

    def _evict_locked(self) -> None:
        """Evict least-recently-used idle models until below the cap.

        Called under ``self._lock`` before inserting a new model. Only models at
        zero references are evictable; if every resident model is in use the pool
        is allowed to exceed the cap rather than evict a live stream's model.
        """
        while len(self._entries) >= self._max_loaded:
            victim: Optional[str] = None
            for signature, entry in self._entries.items():
                if entry.refcount == 0:
                    victim = signature
                    break
            if victim is None:
                return
            entry = self._entries.pop(victim)
            self._unload(entry)
        metrics.MODEL_LOADED_MODELS.set(len(self._entries))

    def _load(self, signature: str, config: DetectorConfig) -> Detector:
        """Build a detector for ``config``, timing the load."""
        started = time.perf_counter()
        detector = self._factory(config)
        metrics.MODEL_LOAD_SECONDS.observe(time.perf_counter() - started)
        logger.info(
            f"Model loaded: signature={signature[:64]} "
            f"resident={len(self._entries) + 1}"
        )
        return detector

    def _unload(self, entry: _Entry) -> None:
        """Release a detector's model/provider references, timing the unload."""
        started = time.perf_counter()
        self._release_detector(entry.detector)
        metrics.MODEL_UNLOAD_SECONDS.observe(time.perf_counter() - started)
        logger.info(f"Model unloaded: signature={entry.signature[:64]}")

    @staticmethod
    def _release_detector(detector: Detector) -> None:
        """Drop model/provider references so accelerator memory is reclaimed."""
        try:
            detector.model = None
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass
        try:
            detector.provider = None
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass


_global_lock = threading.Lock()
_global_manager: Optional[ModelManager] = None


def get_model_manager(
    max_loaded: int = 4, factory: DetectorFactory = Detector
) -> ModelManager:
    """Return the process-wide model manager, building it on first use.

    ``max_loaded`` and ``factory`` apply only when the singleton is first
    constructed; later calls return the existing instance unchanged.
    """
    global _global_manager
    with _global_lock:
        if _global_manager is None:
            _global_manager = ModelManager(max_loaded=max_loaded, factory=factory)
        return _global_manager


def shutdown_model_manager() -> None:
    """Unload and drop the process-wide model manager (service shutdown)."""
    global _global_manager
    with _global_lock:
        manager = _global_manager
        _global_manager = None
    if manager is not None:
        manager.shutdown()


def reset_model_manager() -> None:
    """Drop the process-wide manager reference without unloading (test helper)."""
    global _global_manager
    with _global_lock:
        _global_manager = None
