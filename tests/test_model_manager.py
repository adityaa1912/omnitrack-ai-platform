from __future__ import annotations

import threading
import time

import pytest

from backend.settings import Settings
from backend.model_manager import (
    ModelManager,
    get_model_manager,
    reset_model_manager,
    shutdown_model_manager,
)
from backend.batching import BatchManager, detector_signature
from backend.observability import metrics
from inference.config import DetectorConfig


class _FakeDetector:
    """Detector stand-in: carries the model/provider refs the pool nulls."""

    def __init__(self, config: DetectorConfig) -> None:
        self.config = config
        self.model = object()
        self.provider = object()


class _Factory:
    """Counting detector factory: records every detector it builds."""

    def __init__(self) -> None:
        self.created: list = []
        self._lock = threading.Lock()

    def __call__(self, config: DetectorConfig) -> _FakeDetector:
        detector = _FakeDetector(config)
        with self._lock:
            self.created.append(detector)
        return detector


def _cfg(model: str = "a.pt", device: str = "cpu", conf: float = 0.5) -> DetectorConfig:
    return DetectorConfig(model_name=model, device=device, confidence_threshold=conf)


def _hits() -> float:
    return metrics.MODEL_CACHE_HITS_TOTAL._value.get()


def _misses() -> float:
    return metrics.MODEL_CACHE_MISSES_TOTAL._value.get()


@pytest.fixture(autouse=True)
def _clean_global():
    reset_model_manager()
    yield
    reset_model_manager()


def test_settings_accept_model_manager_knobs() -> None:
    s = Settings(
        _env_file=None,
        model_manager_enabled=True,
        model_manager_max_loaded=6,
    )
    assert s.model_manager_enabled is True
    assert s.model_manager_max_loaded == 6


def test_settings_reject_zero_max_loaded() -> None:
    with pytest.raises(Exception):
        Settings(_env_file=None, model_manager_max_loaded=0)


def test_acquire_loads_once_and_shares() -> None:
    factory = _Factory()
    manager = ModelManager(max_loaded=4, factory=factory)
    hits, misses = _hits(), _misses()

    d1 = manager.acquire(_cfg())
    d2 = manager.acquire(_cfg())

    assert d1 is d2  # one shared detector
    assert len(factory.created) == 1  # loaded exactly once
    assert manager.loaded_count() == 1
    assert _misses() - misses == 1
    assert _hits() - hits == 1


def test_release_keeps_model_warm_then_hits() -> None:
    factory = _Factory()
    manager = ModelManager(max_loaded=4, factory=factory)

    signature = detector_signature(_cfg())
    manager.acquire(_cfg())
    manager.acquire(_cfg())
    manager.release(signature)
    # One reference remains; the model is obviously still resident.
    assert manager.loaded_count() == 1

    manager.release(signature)
    # Refcount is now zero, but the model stays warm (not unloaded on release).
    assert manager.loaded_count() == 1
    assert manager.stats()["in_use"] == 0

    hits = _hits()
    reused = manager.acquire(_cfg())
    assert reused is factory.created[0]
    assert len(factory.created) == 1  # served warm, no reload
    assert _hits() - hits == 1


def test_lru_eviction_over_cap_unloads_idle_model() -> None:
    factory = _Factory()
    manager = ModelManager(max_loaded=2, factory=factory)

    for i in range(3):
        cfg = _cfg(model=f"m{i}.pt")
        manager.acquire(cfg)
        manager.release(detector_signature(cfg))

    # Only the cap's worth stay resident; the least-recently-used is evicted.
    assert manager.loaded_count() == 2
    resident = manager.signatures()
    assert detector_signature(_cfg(model="m0.pt")) not in resident
    assert detector_signature(_cfg(model="m2.pt")) in resident
    # The evicted detector had its heavy references dropped (memory-safe unload).
    assert factory.created[0].provider is None
    assert factory.created[0].model is None


def test_in_use_model_is_never_evicted() -> None:
    factory = _Factory()
    manager = ModelManager(max_loaded=1, factory=factory)

    cfg_a = _cfg(model="a.pt")
    cfg_b = _cfg(model="b.pt")
    manager.acquire(cfg_a)  # held (refcount 1)
    manager.acquire(cfg_b)  # a is in use, so pool exceeds the cap rather than evict it

    assert manager.loaded_count() == 2
    assert factory.created[0].provider is not None  # a still intact

    manager.release(detector_signature(cfg_a))
    cfg_c = _cfg(model="c.pt")
    manager.acquire(cfg_c)  # now a is idle and least-recently-used -> evicted

    resident = manager.signatures()
    assert detector_signature(cfg_a) not in resident
    assert factory.created[0].provider is None


def test_warm_preloads_without_reference() -> None:
    factory = _Factory()
    manager = ModelManager(max_loaded=4, factory=factory)

    manager.warm(_cfg())
    assert manager.loaded_count() == 1
    assert manager.stats()["in_use"] == 0  # warm: resident but unreferenced

    hits = _hits()
    detector = manager.acquire(_cfg())
    assert detector is factory.created[0]
    assert len(factory.created) == 1  # acquire after warm is a hit
    assert _hits() - hits == 1


def test_provider_aware_cache_key_isolates_configs() -> None:
    factory = _Factory()
    manager = ModelManager(max_loaded=8, factory=factory)

    manager.acquire(_cfg(device="cpu"))
    manager.acquire(_cfg(device="cuda"))
    manager.acquire(_cfg(conf=0.9))

    assert len(factory.created) == 3  # distinct signatures never share a model
    assert manager.loaded_count() == 3


def test_shutdown_unloads_all_models() -> None:
    factory = _Factory()
    manager = ModelManager(max_loaded=8, factory=factory)
    for i in range(3):
        manager.acquire(_cfg(model=f"m{i}.pt"))

    manager.shutdown()

    assert manager.loaded_count() == 0
    assert all(d.provider is None and d.model is None for d in factory.created)


def test_concurrent_acquire_release_is_consistent() -> None:
    factory = _Factory()
    manager = ModelManager(max_loaded=4, factory=factory)
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        for _ in range(50):
            manager.acquire(_cfg())
            manager.release(detector_signature(_cfg()))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert all(not t.is_alive() for t in threads)
    # Serialized load: exactly one model built despite the concurrent storm.
    assert len(factory.created) == 1
    assert manager.loaded_count() == 1
    assert manager.stats()["in_use"] == 0  # every acquire was released


def test_global_singleton_is_shared_and_resettable() -> None:
    factory = _Factory()
    a = get_model_manager(max_loaded=3, factory=factory)
    b = get_model_manager()
    assert a is b  # same process-wide instance

    a.acquire(_cfg())
    shutdown_model_manager()  # unloads and drops the global
    assert factory.created[0].provider is None

    c = get_model_manager(max_loaded=3, factory=factory)
    assert c is not a  # a fresh instance after shutdown


def test_batch_manager_delegates_ownership_to_pool() -> None:
    factory = _Factory()
    pool = ModelManager(max_loaded=4, factory=factory)
    batch_manager = BatchManager(max_size=8, max_wait_ms=10, model_manager=pool)

    cfg = _cfg()
    signature = detector_signature(cfg)
    d1, _ = batch_manager.acquire(cfg)
    d2, _ = batch_manager.acquire(cfg)

    assert d1 is d2
    assert len(factory.created) == 1  # detector built once, owned by the pool
    assert pool.stats()["in_use"] == 1  # both streams -> one in-use signature

    batch_manager.release(signature)
    batch_manager.release(signature)
    # Both references dropped: coordinator gone, model warm in the pool.
    assert batch_manager.active_signatures() == 0
    assert pool.loaded_count() == 1
    assert pool.stats()["in_use"] == 0


def test_batch_manager_without_pool_builds_detectors_itself() -> None:
    # Backward-compatible path: no model manager -> BatchManager owns detectors.
    calls = {"n": 0}

    class _OwnDetector:
        def __init__(self, config) -> None:
            calls["n"] += 1
            self.config = config

    import backend.batching as batching

    original = batching.Detector
    batching.Detector = _OwnDetector
    manager = BatchManager(max_size=4, max_wait_ms=5)
    try:
        cfg = _cfg()
        manager.acquire(cfg)
        manager.acquire(cfg)
        assert calls["n"] == 1  # one shared detector, no pool involved
        assert manager.active_signatures() == 1
    finally:
        manager.stop_all()
        batching.Detector = original
