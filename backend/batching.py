"""
Dynamic batching coordinator: fuse concurrent per-stream forward passes.

With the scheduler enabled (or with many legacy threads), multiple streams that
share an identical detector configuration can have frames ready at the same
moment. Instead of running one isolated forward pass per frame, the
:class:`BatchCoordinator` queues concurrent ``predict`` calls and drains them
into a single fused ``Detector.predict_batch`` — a real batch on torch, a
sequential fallback on other providers. A single stream (or a disabled
deployment) never pays for this: the coordinator uses a direct single-frame fast
path and the whole module is only imported when batching is enabled.

Invariants preserved:
* Per-stream ordering. Each stream's caller blocks until ITS OWN frame's result
  is ready; frames of one stream are never reordered because the coordinator
  assigns results back to the exact request that produced them.
* One forward pass in flight per shared detector (a per-coordinator compute lock
  guards ``predict_batch``), matching the single-tenant model contract.
* Graceful shutdown: ``stop`` posts a sentinel, joins the batch thread, and
  drains any still-pending requests with an error so no caller hangs.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from inference.config import DetectorConfig
from inference.detector import Detector
from inference.types import Frame, InferenceResult
from .observability import metrics
from .observability.logging import get_logger


logger = get_logger(__name__, component="inference.batching")


@dataclass
class _Pending:
    """One queued request: the frame, the caller's wait slot, and the result."""

    frame: Frame
    slot: "threading.Event" = field(default_factory=threading.Event)
    result: Optional[InferenceResult] = None
    error: Optional[BaseException] = None


class BatchCoordinator:
    """Coalesces concurrent ``predict`` calls for ONE shared detector.

    ``predict`` is called by every stream thread/worker pinned to this detector
    signature. When more than one stream is registered, calls queue and a single
    batch thread drains them into one fused forward pass (bounded by
    ``max_size`` and ``max_wait_seconds``); with one stream registered it falls
    back to a direct ``detector.predict`` so the fast path never waits.
    """

    def __init__(
        self,
        detector: Detector,
        max_size: int,
        max_wait_seconds: float,
        signature: str,
    ) -> None:
        self.detector = detector
        self.max_size = max(max_size, 1)
        self.max_wait_seconds = max(max_wait_seconds, 0.0)
        self.signature = signature

        self._stream_count = 0
        self._count_lock = threading.Lock()
        self._queue: "List[_Pending]" = []
        self._queue_cond = threading.Condition()
        # A compute lock keeps exactly one fused forward pass in flight per
        # shared detector, matching the single-tenant model contract.
        self._compute_lock = threading.Lock()
        self._stopping = False

        self._thread = threading.Thread(
            target=self._batch_loop, daemon=True, name=f"batch-{signature[:24]}"
        )
        self._thread.start()

    def register(self) -> None:
        with self._count_lock:
            self._stream_count += 1

    def unregister(self) -> None:
        with self._count_lock:
            self._stream_count = max(self._stream_count - 1, 0)

    @property
    def stream_count(self) -> int:
        with self._count_lock:
            return self._stream_count

    def predict(self, frame: Frame) -> InferenceResult:
        """Return this frame's inference result, batching concurrent calls.

        Raises the batch error (if any) so the caller's normal per-frame error
        handling applies.
        """
        # Single-frame fast path: never queue or wait when this signature has a
        # lone stream (or is between registrations).
        if self.stream_count <= 1:
            return self.detector.predict(frame)

        pending = _Pending(frame=frame)
        with self._queue_cond:
            if self._stopping:
                raise RuntimeError("batch coordinator is stopping")
            self._queue.append(pending)
            self._queue_cond.notify()
        # Block until the batch thread scatters this request's result/error.
        pending.slot.wait()
        if pending.error is not None:
            raise pending.error
        assert pending.result is not None
        return pending.result

    def stop(self) -> None:
        """Signal the batch thread to exit and drain pending waiters."""
        with self._queue_cond:
            self._stopping = True
            self._queue_cond.notify()
        self._thread.join(timeout=5.0)

    def _batch_loop(self) -> None:
        """Drain queued requests into bounded fused forward passes."""
        while True:
            with self._queue_cond:
                while not self._queue and not self._stopping:
                    self._queue_cond.wait()
                if not self._queue:
                    return  # stopping and drained
                # Greedily grab up to max_size, capped by the wait deadline so
                # stragglers do not delay an already-waiting batch.
                batch = [self._queue.pop(0)]
                deadline = time.perf_counter() + self.max_wait_seconds
                while len(batch) < self.max_size and self._queue:
                    if self._queue[0].slot.is_set():
                        break  # a waiter aborted; leave it for the next drain
                    if time.perf_counter() >= deadline:
                        break
                    batch.append(self._queue.pop(0))
            self._dispatch(batch)

    def _dispatch(self, batch: List[_Pending]) -> None:
        """Run one fused forward pass and scatter results back to the waiters."""
        started = time.perf_counter()
        try:
            with self._compute_lock:
                results = self.detector.predict_batch([p.frame for p in batch])
        except BaseException as exc:  # noqa: BLE001 - propagate to each caller
            for pending in batch:
                pending.error = exc
                pending.slot.set()
            return
        elapsed = time.perf_counter() - started
        metrics.BATCH_SIZE.observe(len(batch))
        metrics.BATCH_LATENCY.observe(elapsed)
        metrics.BATCHING_EFFICIENCY.set(len(batch) / self.max_size)
        for pending, result in zip(batch, results):
            pending.result = result
            pending.slot.set()


def detector_signature(config: DetectorConfig) -> str:
    """Stable key identifying a detector configuration.

    Streams that share a signature may batch together; streams with different
    signatures never share a model, so heterogeneous deployments stay correct.
    """
    return (
        f"{config.model_name}|{config.device}|{config.inference_provider}|"
        f"{config.confidence_threshold}|{config.iou_threshold}|"
        f"{config.inference_width}|{config.inference_height}|"
        f"{config.enable_fp16}|{config.benchmark_enabled}"
    )


class BatchManager:
    """Refcounted registry of shared detectors keyed by detector signature.

    ``acquire`` builds, loads and warms one :class:`Detector` per signature the
    first time it is requested and hands out the shared instance plus its
    coordinator; ``release`` drops a stream's reference and stops the coordinator
    when the last reference goes away. ``stop_all`` shuts every coordinator down
    (used by the service at shutdown).
    """

    def __init__(self, max_size: int, max_wait_ms: int) -> None:
        self._max_size = max(max_size, 1)
        self._max_wait_seconds = max(max_wait_ms, 0) / 1000.0
        self._coordinator: Dict[str, BatchCoordinator] = {}
        self._lock = threading.Lock()

    def acquire(self, config: DetectorConfig) -> tuple[Detector, BatchCoordinator]:
        """Return the shared detector and coordinator for ``config``."""
        signature = detector_signature(config)
        with self._lock:
            coordinator = self._coordinator.get(signature)
            if coordinator is None:
                detector = Detector(config)
                coordinator = BatchCoordinator(
                    detector, self._max_size, self._max_wait_seconds, signature
                )
                self._coordinator[signature] = coordinator
            else:
                detector = coordinator.detector
            coordinator.register()
            return detector, coordinator

    def release(self, signature: str) -> None:
        """Drop one stream's reference to the shared detector for ``signature``."""
        with self._lock:
            coordinator = self._coordinator.get(signature)
            if coordinator is None:
                return
            coordinator.unregister()
            if coordinator.stream_count == 0:
                self._coordinator.pop(signature, None)
                coordinator.stop()

    def stop_all(self) -> None:
        """Stop every coordinator (service shutdown)."""
        with self._lock:
            coordinators = list(self._coordinator.values())
            self._coordinator.clear()
        for coordinator in coordinators:
            coordinator.stop()

    def active_signatures(self) -> int:
        with self._lock:
            return len(self._coordinator)
