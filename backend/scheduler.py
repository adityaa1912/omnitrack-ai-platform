"""
Central inference scheduler with a bounded, shared worker pool.

Opt-in alternative to the one-thread-per-stream model in ``service.py``. It
decouples frame capture from the infer/track/render stage: capture threads
``submit`` frames into per-stream channels, and a fixed pool of worker threads
drains those channels. This bounds inference concurrency (N streams no longer
spawn N inference threads all contending for one accelerator) and stops one
slow stream from blocking others.

Ordering & isolation invariants preserved from the legacy loop:
* Each stream is pinned to exactly one worker (least-loaded at register time),
  so its frames are processed strictly in order and its stateful tracker /
  event engine are only ever touched by that single worker.
* That worker owns a thread-local DB session, matching the per-thread session
  contract of ``_store_detections`` / ``_finalize_session``.

Backpressure & recency:
* Each stream channel is a bounded deque. On overflow the oldest queued frame
  is dropped (counted in ``DROPPED_FRAMES_TOTAL``) so the newest frame wins and
  latency stays bounded — a slow consumer never back-pressures capture.
* A worker takes the newest queued frame and discards the rest (also counted),
  matching the adaptive-drop behavior of the inline pipeline.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

from inference.types import Frame
from .observability import metrics
from .observability.logging import get_logger


logger = get_logger(__name__, component="inference.scheduler")

# Bounded wait for a stream's in-flight frame to finish during unregister,
# matching the service-level stream stop timeout (OMNITRACK_STREAM_STOP_TIMEOUT).
_UNREGISTER_TIMEOUT = 5.0

# A per-frame processor: ``(frame, db) -> None``. Runs on the owning worker
# thread with that worker's DB session. Raising signals a terminal stream error.
ProcessFn = Callable[[Frame, object], None]


class _StreamChannel:
    """Bounded input channel for one stream, pinned to one worker."""

    def __init__(
        self, stream_id: str, capacity: int, process_fn: ProcessFn, worker_index: int
    ) -> None:
        self.stream_id = stream_id
        self.capacity = capacity
        self.process_fn = process_fn
        self.worker_index = worker_index
        # (frame, submit_perf_counter) newest-last.
        self.pending: Deque[Tuple[Frame, float]] = deque()
        self.in_flight = False
        self.enqueued = False

    def take_newest(self) -> Optional[Tuple[Frame, float]]:
        """Return the newest queued frame, dropping (and counting) the rest."""
        if not self.pending:
            return None
        newest = self.pending.pop()
        dropped = len(self.pending)
        if dropped:
            self.pending.clear()
            metrics.DROPPED_FRAMES_TOTAL.labels(stream_id=self.stream_id).inc(dropped)
        return newest


class InferenceScheduler:
    """Fixed pool of worker threads draining per-stream input channels.

    Constructed lazily by :class:`InferenceService` only when scheduling is
    enabled, so the default deployment never builds a pool (startup stays lazy).
    """

    def __init__(
        self,
        session_factory,
        num_workers: int = 2,
        stream_queue_capacity: int = 2,
    ) -> None:
        self._session_factory = session_factory
        self._num_workers = max(int(num_workers), 1)
        self._capacity = max(int(stream_queue_capacity), 1)

        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._channels: Dict[str, _StreamChannel] = {}
        # Ready stream ids per worker (round-robin fairness within a worker).
        self._ready: List[Deque[str]] = [deque() for _ in range(self._num_workers)]
        # Live channel count per worker, used for least-loaded assignment.
        self._load: List[int] = [0] * self._num_workers
        # Back-reference used to flip a stream's terminal flag on fatal error.
        self._owners: Dict[str, object] = {}

        self._busy = 0
        self._stopping = False
        self._workers: List[threading.Thread] = []
        for i in range(self._num_workers):
            thread = threading.Thread(
                target=self._worker_loop, args=(i,), daemon=True
            )
            self._workers.append(thread)
            thread.start()
        logger.info(
            f"Inference scheduler started: workers={self._num_workers} "
            f"stream_queue_capacity={self._capacity}"
        )

    def register(self, stream_id: str, process_fn: ProcessFn, owner: object = None) -> None:
        """Create a channel for ``stream_id`` pinned to the least-loaded worker."""
        with self._lock:
            if stream_id in self._channels:
                return
            worker_index = min(range(self._num_workers), key=lambda i: self._load[i])
            self._channels[stream_id] = _StreamChannel(
                stream_id, self._capacity, process_fn, worker_index
            )
            self._load[worker_index] += 1
            if owner is not None:
                self._owners[stream_id] = owner
        metrics.SCHEDULER_QUEUE_DEPTH.labels(stream_id=stream_id).set(0)

    def submit(self, stream_id: str, frame: Frame) -> None:
        """Queue a frame for a registered stream, dropping the oldest on overflow."""
        # Stamp the arrival time before taking the lock: it is independent of
        # scheduler state, so keep the (hot-path) critical section minimal.
        submit_ts = time.perf_counter()
        with self._cond:
            # Once stopping, accept no new work: a capture thread still alive
            # past its join timeout must not keep feeding a draining pool.
            if self._stopping:
                return
            channel = self._channels.get(stream_id)
            if channel is None:
                return
            channel.pending.append((frame, submit_ts))
            over = len(channel.pending) - channel.capacity
            if over > 0:
                for _ in range(over):
                    channel.pending.popleft()
                metrics.DROPPED_FRAMES_TOTAL.labels(stream_id=stream_id).inc(over)
            depth = len(channel.pending)
            if not channel.in_flight and not channel.enqueued:
                channel.enqueued = True
                self._ready[channel.worker_index].append(stream_id)
                self._cond.notify_all()
        metrics.SCHEDULER_QUEUE_DEPTH.labels(stream_id=stream_id).set(depth)

    def unregister(self, stream_id: str) -> None:
        """Drop a stream's channel and free its worker slot.

        Waits (bounded) for any in-flight frame to finish first, so the capture
        thread's teardown (`_cleanup`: detector/frame-source release) never races
        the pinned worker still inside `process_fn`. The worker clears
        `in_flight` under `_cond`, so removing the channel afterwards guarantees
        no worker re-enters this stream's processor.
        """
        deadline = time.perf_counter() + _UNREGISTER_TIMEOUT
        with self._cond:
            channel = self._channels.get(stream_id)
            while (
                channel is not None
                and channel.in_flight
                and not self._stopping
                and time.perf_counter() < deadline
            ):
                self._cond.wait(timeout=0.05)
            channel = self._channels.pop(stream_id, None)
            self._owners.pop(stream_id, None)
            if channel is not None:
                self._load[channel.worker_index] = max(
                    self._load[channel.worker_index] - 1, 0
                )
        if channel is not None:
            metrics.SCHEDULER_QUEUE_DEPTH.labels(stream_id=stream_id).set(0)

    def stop(self) -> None:
        """Signal all workers to exit and join them (bounded)."""
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        for thread in self._workers:
            thread.join(timeout=5.0)
        logger.info("Inference scheduler stopped")

    def _worker_loop(self, worker_index: int) -> None:
        """Drain this worker's ready channels until stopped, owning one session."""
        db = self._session_factory()
        ready = self._ready[worker_index]
        try:
            while True:
                with self._cond:
                    while not ready and not self._stopping:
                        self._cond.wait()
                    if self._stopping and not ready:
                        return
                    stream_id = ready.popleft()
                    channel = self._channels.get(stream_id)
                    if channel is None:
                        continue
                    item = channel.take_newest()
                    if item is None:
                        channel.enqueued = False
                        continue
                    channel.enqueued = False
                    channel.in_flight = True
                    self._busy += 1
                    busy = self._busy
                frame, submit_ts = item
                pickup_ts = time.perf_counter()
                metrics.SCHEDULER_WORKER_UTILIZATION.set(busy / self._num_workers)
                metrics.SCHEDULER_LATENCY.labels(stream_id=stream_id).observe(
                    max(pickup_ts - submit_ts, 0.0)
                )
                self._run_one(stream_id, channel, frame, db)
                done_ts = time.perf_counter()
                metrics.SCHEDULER_STREAM_LATENCY.labels(stream_id=stream_id).observe(
                    max(done_ts - submit_ts, 0.0)
                )
                with self._cond:
                    channel.in_flight = False
                    self._busy = max(self._busy - 1, 0)
                    busy = self._busy
                    if (
                        not self._stopping
                        and channel.pending
                        and not channel.enqueued
                        and stream_id in self._channels
                    ):
                        channel.enqueued = True
                        ready.append(stream_id)
                        self._cond.notify_all()
                metrics.SCHEDULER_WORKER_UTILIZATION.set(busy / self._num_workers)
        finally:
            try:
                db.commit()
            except Exception:  # noqa: BLE001 - best-effort tail flush on exit
                db.rollback()
            self._session_factory.remove()

    def _run_one(self, stream_id: str, channel: _StreamChannel, frame: Frame, db) -> None:
        """Invoke a stream's processor for one frame; terminate it on failure."""
        try:
            channel.process_fn(frame, db)
        except Exception as exc:  # noqa: BLE001 - contained per-stream failure
            logger.error(
                f"Scheduler processing error for stream {stream_id}: {exc}"
            )
            metrics.STREAM_ERRORS_TOTAL.labels(stream_id=stream_id).inc()
            owner = self._owners.get(stream_id)
            if owner is not None:
                owner.metrics.is_active = False
                owner.metrics.error_message = str(exc)
                owner.is_running = False
