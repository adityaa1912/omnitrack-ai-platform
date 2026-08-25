from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .logging import get_logger
from . import metrics as om

logger = get_logger(__name__, component="backend.supervisor")


@dataclass
class _WorkerRegistration:
    name: str
    obj: Any
    thread_attr: str
    max_restarts: int = 5
    restart_delay_seconds: float = 2.0
    _restarts: int = field(default=0, init=False)
    _failed: bool = field(default=False, init=False)

    def is_alive(self) -> bool:
        t = getattr(self.obj, self.thread_attr, None)
        return t is not None and t.is_alive()

    def attempt_restart(self) -> None:
        try:
            if getattr(self.obj, "_running", False):
                self.obj._running = False
            self.obj.start()
            self._restarts += 1
            self._failed = False
            om.WORKER_RESTARTS_TOTAL.labels(worker=self.name).inc()
            om.WORKER_HEALTH.labels(worker=self.name).set(1)
            logger.info(
                "Worker restarted by supervisor",
                extra={"fields": {"worker": self.name, "restarts": self._restarts}},
            )
        except Exception as exc:
            logger.error(
                "Supervisor failed to restart worker %s",
                self.name,
                exc_info=exc,
                extra={"fields": {"worker": self.name}},
            )

    def health_snapshot(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "alive": self.is_alive(),
            "restarts": self._restarts,
            "healthy": not self._failed,
        }


class WorkerSupervisor:
    def __init__(self, check_interval_seconds: float = 5.0) -> None:
        self._interval = max(check_interval_seconds, 1.0)
        self._registrations: List[_WorkerRegistration] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def register(
        self,
        name: str,
        obj: Any,
        thread_attr: str,
        max_restarts: int = 5,
        restart_delay_seconds: float = 2.0,
    ) -> None:
        with self._lock:
            self._registrations.append(
                _WorkerRegistration(
                    name=name,
                    obj=obj,
                    thread_attr=thread_attr,
                    max_restarts=max_restarts,
                    restart_delay_seconds=restart_delay_seconds,
                )
            )
        om.WORKER_HEALTH.labels(worker=name).set(1)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop, name="worker-supervisor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 2.0)
            self._thread = None

    def worker_snapshots(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [r.health_snapshot() for r in self._registrations]

    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(self._interval)
            if self._stop.is_set():
                break
            with self._lock:
                registrations = list(self._registrations)
            for reg in registrations:
                self._check_worker(reg)

    def _check_worker(self, reg: _WorkerRegistration) -> None:
        try:
            running = getattr(reg.obj, "_running", True)
            if not running:
                om.WORKER_HEALTH.labels(worker=reg.name).set(0)
                return
            if reg.is_alive():
                om.WORKER_HEALTH.labels(worker=reg.name).set(1)
                return
            if reg._failed:
                return
            om.WORKER_FAILURES_TOTAL.labels(worker=reg.name).inc()
            om.WORKER_HEALTH.labels(worker=reg.name).set(0)
            reg._failed = True
            logger.warning(
                "Background worker thread died unexpectedly",
                extra={"fields": {"worker": reg.name}},
            )
            if reg._restarts < reg.max_restarts:
                time.sleep(reg.restart_delay_seconds)
                reg.attempt_restart()
        except Exception as exc:
            logger.warning(
                "Supervisor check error for worker %s",
                reg.name,
                exc_info=exc,
                extra={"fields": {"worker": reg.name}},
            )
