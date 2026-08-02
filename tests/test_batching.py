from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from backend.settings import Settings
from backend.batching import BatchCoordinator, detector_signature
from backend.inference.providers.base import InferenceProvider
from inference.config import DetectorConfig
from inference.types import Frame, InferenceResult


def _frame(frame_id: int) -> Frame:
    return Frame(
        timestamp=float(frame_id),
        frame_id=frame_id,
        data=np.zeros((2, 2, 3), dtype=np.uint8),
    )


class _FakeDetector:
    """Stand-in exposing only what BatchCoordinator uses: predict/predict_batch."""

    def __init__(self, gate: "threading.Event" = None) -> None:
        self.predict_calls = 0
        self.batch_calls = 0
        self.batch_sizes = []
        self._gate = gate
        self._lock = threading.Lock()

    def predict(self, frame: Frame) -> InferenceResult:
        with self._lock:
            self.predict_calls += 1
        return InferenceResult(frame=frame, detections=[], inference_time_ms=0.0)

    def predict_batch(self, frames):
        if self._gate is not None:
            self._gate.wait(timeout=5.0)
        with self._lock:
            self.batch_calls += 1
            self.batch_sizes.append(len(frames))
        return [
            InferenceResult(frame=f, detections=[], inference_time_ms=0.0)
            for f in frames
        ]


class _FakeProvider(InferenceProvider):
    name = "fake"

    def load(self) -> None:
        pass

    def warmup(self) -> None:
        pass

    def predict(self, data: np.ndarray):
        return [(0.0, 0.0, 1.0, 1.0, 0.9, int(data[0, 0, 0]))]

    def class_names(self):
        return {}


def test_settings_accept_batching_knobs() -> None:
    s = Settings(
        _env_file=None,
        batching_enabled=True,
        batch_max_size=16,
        batch_max_wait_ms=25,
    )
    assert s.batching_enabled is True
    assert s.batch_max_size == 16
    assert s.batch_max_wait_ms == 25


def test_settings_reject_zero_batch_size() -> None:
    with pytest.raises(Exception):
        Settings(_env_file=None, batch_max_size=0)


def test_provider_base_predict_batch_is_sequential() -> None:
    provider = _FakeProvider(DetectorConfig())
    images = [np.full((1, 1, 3), i, dtype=np.uint8) for i in range(3)]
    out = provider.predict_batch(images)
    assert len(out) == 3
    # Each image decoded independently, order preserved (class_id == pixel).
    assert [o[0][5] for o in out] == [0, 1, 2]


def test_single_stream_uses_fast_path() -> None:
    detector = _FakeDetector()
    coordinator = BatchCoordinator(detector, max_size=8, max_wait_seconds=0.05, signature="s")
    coordinator.register()  # one stream
    try:
        result = coordinator.predict(_frame(1))
        assert result.frame.frame_id == 1
        assert detector.predict_calls == 1
        assert detector.batch_calls == 0
    finally:
        coordinator.stop()


def test_concurrent_calls_fuse_into_one_batch() -> None:
    gate = threading.Event()
    detector = _FakeDetector(gate=gate)
    coordinator = BatchCoordinator(detector, max_size=8, max_wait_seconds=0.2, signature="s")
    coordinator.register()
    coordinator.register()  # two streams -> batching path
    results: dict = {}

    def call(fid: int) -> None:
        results[fid] = coordinator.predict(_frame(fid))

    threads = [threading.Thread(target=call, args=(i,)) for i in range(4)]
    try:
        for t in threads:
            t.start()
        # Let all four enqueue and the batch thread collect them, then release.
        time.sleep(0.1)
        gate.set()
        for t in threads:
            t.join(timeout=5.0)

        # Every caller received ITS OWN frame's result (ordering/identity).
        assert set(results.keys()) == {0, 1, 2, 3}
        for fid, res in results.items():
            assert res.frame.frame_id == fid
        # A single fused forward pass served the concurrent calls.
        assert detector.batch_calls == 1
        assert detector.batch_sizes == [4]
        assert detector.predict_calls == 0
    finally:
        gate.set()
        coordinator.stop()


def test_batch_respects_max_size() -> None:
    gate = threading.Event()
    detector = _FakeDetector(gate=gate)
    coordinator = BatchCoordinator(detector, max_size=2, max_wait_seconds=0.5, signature="s")
    coordinator.register()
    coordinator.register()
    results: dict = {}

    def call(fid: int) -> None:
        results[fid] = coordinator.predict(_frame(fid))

    threads = [threading.Thread(target=call, args=(i,)) for i in range(4)]
    try:
        for t in threads:
            t.start()
        time.sleep(0.1)
        gate.set()
        for t in threads:
            t.join(timeout=5.0)
        assert set(results.keys()) == {0, 1, 2, 3}
        # max_size=2 caps each fused pass; no batch exceeds the cap.
        assert detector.batch_calls >= 2
        assert all(size <= 2 for size in detector.batch_sizes)
        assert sum(detector.batch_sizes) == 4
    finally:
        gate.set()
        coordinator.stop()


def test_stop_drains_pending_waiters_on_error() -> None:
    # A batch thread blocked in predict_batch is stopped; the enqueued caller
    # must not hang. Use a gate that is never set, and stop while blocked.
    gate = threading.Event()
    detector = _FakeDetector(gate=gate)
    coordinator = BatchCoordinator(detector, max_size=8, max_wait_seconds=0.05, signature="s")
    coordinator.register()
    coordinator.register()
    outcome: dict = {}

    def call() -> None:
        try:
            outcome["result"] = coordinator.predict(_frame(1))
        except BaseException as exc:  # noqa: BLE001 - recording for assertion
            outcome["error"] = exc

    t = threading.Thread(target=call)
    t.start()
    time.sleep(0.1)  # let it enqueue and the batch thread block on the gate
    gate.set()  # release the in-flight forward pass so the thread can exit
    coordinator.stop()
    t.join(timeout=5.0)
    assert not t.is_alive()


def test_detector_signature_groups_by_config() -> None:
    a = DetectorConfig(model_name="m.pt", device="cpu", confidence_threshold=0.5)
    b = DetectorConfig(model_name="m.pt", device="cpu", confidence_threshold=0.5)
    c = DetectorConfig(model_name="m.pt", device="cuda", confidence_threshold=0.5)
    assert detector_signature(a) == detector_signature(b)
    assert detector_signature(a) != detector_signature(c)
