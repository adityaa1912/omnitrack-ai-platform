from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from ..observability import metrics as om
from ..observability.logging import get_logger
from .models import NotificationAttempt

logger = get_logger(__name__, component="backend.alerts")


class NotificationProvider:
    name = "base"

    def send(self, payload: Dict[str, Any]) -> None:
        raise NotImplementedError


class InAppNotificationProvider(NotificationProvider):
    name = "in_app"

    def __init__(self, sink: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self._sink = sink
        self._lock = threading.Lock()

    def set_sink(self, sink: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            self._sink = sink

    def send(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            sink = self._sink
        if sink is not None:
            sink(payload)


class WebhookNotificationProvider(NotificationProvider):
    name = "webhook"

    def __init__(self, url: str, *, timeout_seconds: float = 5.0) -> None:
        self._url = url
        self._timeout = timeout_seconds

    def send(self, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise RuntimeError(f"webhook returned status {status}")


class NotificationDispatcher:
    def __init__(
        self,
        providers: List[NotificationProvider],
        session_factory,
        *,
        max_retries: int = 3,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        self._providers = providers
        self._session_factory = session_factory
        self._max_retries = max_retries
        self._retry_delay = retry_delay_seconds

    def dispatch(self, alert_instance_id: int, payload: Dict[str, Any]) -> None:
        for provider in self._providers:
            self._dispatch_one(provider, alert_instance_id, payload)

    def _dispatch_one(
        self, provider: NotificationProvider, alert_instance_id: int, payload: Dict[str, Any]
    ) -> None:
        attempt = 0
        while True:
            attempt += 1
            try:
                provider.send(payload)
                self._record(alert_instance_id, provider.name, "success", attempt, None, payload)
                om.ALERT_NOTIFICATIONS_SUCCESS_TOTAL.inc()
                return
            except Exception as exc:  # noqa: BLE001
                if attempt <= self._max_retries:
                    self._record(
                        alert_instance_id, provider.name, "retrying", attempt, str(exc), None
                    )
                    time.sleep(self._retry_delay)
                    continue
                self._record(
                    alert_instance_id, provider.name, "failed", attempt, str(exc), None
                )
                om.ALERT_NOTIFICATIONS_FAILURE_TOTAL.inc()
                logger.warning(
                    f"Alert notification failed provider={provider.name} "
                    f"alert={alert_instance_id}: {exc}"
                )
                return

    def _record(
        self,
        alert_instance_id: int,
        provider: str,
        status: str,
        attempt_number: int,
        error: Optional[str],
        payload: Optional[Dict[str, Any]],
    ) -> None:
        session = self._session_factory()
        try:
            row = NotificationAttempt(
                alert_instance_id=alert_instance_id,
                provider=provider,
                status=status,
                attempt_number=attempt_number,
                error=error,
                payload=payload,
            )
            session.add(row)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.warning(f"Failed recording notification attempt: {exc}")
        finally:
            session.close()
