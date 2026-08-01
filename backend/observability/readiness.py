"""
Readiness / liveness probing and process system sampling.

Separates three concerns that are often conflated under a single "health":

* **Liveness** (``/live``) — is the process up and its event loop responsive?
  Always cheap; never touches dependencies. A failure means the process should
  be restarted.
* **Readiness** (``/ready``) — is the service able to do useful work right now?
  Checks every dependency a request would rely on: model availability, stream
  manager integrity, database reachability, and configuration validity. A
  failure means "don't route traffic yet" (e.g. still warming up or a
  dependency is down), not "restart me".
* **Health** (``/health``) — the legacy aggregate endpoint, kept for backward
  compatibility. It reports coarse status and active-stream count.

The readiness checks are registered as named callables returning
:class:`CheckResult`. Each is defensive: a check that itself raises is reported
as a failed check rather than crashing the probe.

This module also owns the background system sampler that periodically records
CPU/memory/thread metrics into the Prometheus registry, off the request path.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import psutil

from . import metrics


@dataclass
class CheckResult:
    """Outcome of a single readiness check."""

    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class ReadinessReport:
    """Aggregate of all readiness checks."""

    ready: bool
    checks: List[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ready": self.ready,
            "checks": [c.to_dict() for c in self.checks],
        }


# A readiness check returns a CheckResult. It must be fast and non-blocking.
CheckFn = Callable[[], CheckResult]


class ReadinessProbe:
    """Registry and evaluator of named readiness checks.

    Checks are registered once at startup (order preserved) and evaluated on
    each ``/ready`` call. Evaluation is sequential and defensive; checks are
    expected to be cheap (a ping, a flag read), never a heavy operation.
    """

    def __init__(self) -> None:
        self._checks: List[CheckFn] = []
        self._lock = threading.Lock()

    def register(self, check: CheckFn) -> None:
        """Register a readiness check (evaluated in registration order)."""
        with self._lock:
            self._checks.append(check)

    def evaluate(self) -> ReadinessReport:
        """Run every check and aggregate the results."""
        with self._lock:
            checks = list(self._checks)

        results: List[CheckResult] = []
        for check in checks:
            try:
                results.append(check())
            except Exception as exc:  # noqa: BLE001 - a failing check is data
                name = getattr(check, "__name__", "unknown")
                results.append(
                    CheckResult(name=name, ok=False, detail=f"check raised: {exc}")
                )
        ready = all(r.ok for r in results)
        return ReadinessReport(ready=ready, checks=results)


# ---------------------------------------------------------------------------
# Background system sampler
# ---------------------------------------------------------------------------


class SystemSampler:
    """Periodically records process CPU/memory/thread metrics.

    Runs on a daemon thread with a modest interval so the request path never
    pays for ``psutil`` calls. ``psutil.cpu_percent`` is non-blocking after the
    first priming call (it computes a delta since the previous call).
    """

    def __init__(self, interval_seconds: float = 5.0) -> None:
        self._interval = max(interval_seconds, 0.5)
        self._process = psutil.Process()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the sampler thread (idempotent)."""
        if self._thread is not None:
            return
        # Prime cpu_percent so subsequent calls return a real delta.
        try:
            self._process.cpu_percent(interval=None)
        except Exception:  # noqa: BLE001 - sampling must never crash startup
            pass
        self._thread = threading.Thread(
            target=self._run, name="system-sampler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the sampler thread to exit and wait briefly for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._sample_once()
            except Exception:  # noqa: BLE001 - never let sampling kill the thread
                pass
            self._stop.wait(self._interval)

    def _sample_once(self) -> None:
        metrics.PROCESS_CPU_PERCENT.set(self._process.cpu_percent(interval=None))
        metrics.PROCESS_MEMORY_BYTES.set(self._process.memory_info().rss)
        metrics.PROCESS_THREADS.set(self._process.num_threads())
