from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..observability import metrics as om
from ..observability.logging import get_logger
from .models import AlertInstance, AlertStateHistory
from .notifications import NotificationDispatcher
from .publisher import AlertEventPublisher
from .state import AlertStateStore

logger = get_logger(__name__, component="backend.alerts")

ACTIVE_STATES = ("triggered", "acknowledged", "suppressed")


class AlertTransitionError(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AlertManager:
    def __init__(
        self,
        session_factory,
        state_store: AlertStateStore,
        publisher: AlertEventPublisher,
        dispatcher: NotificationDispatcher,
        *,
        retention_hours: int = 168,
        expiry_interval_seconds: int = 30,
        queue_capacity: int = 1000,
    ) -> None:
        self._session_factory = session_factory
        self._state = state_store
        self._publisher = publisher
        self._dispatcher = dispatcher
        self._retention_hours = retention_hours
        self._expiry_interval = expiry_interval_seconds
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=queue_capacity)
        self._running = False
        self._worker: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        logger.info("Alert manager started")

    def stop(self) -> None:
        self._running = False
        if self._worker is not None:
            self._worker.join(timeout=self._expiry_interval + 5)
        self._drain()

    def trigger(
        self,
        rule: Dict[str, Any],
        computed: Dict[str, Any],
        stream_id: str,
        event: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        dedup_key = rule.get("dedup_key") or f"{rule.get('id')}:{stream_id}"
        session = self._session_factory()
        try:
            existing = (
                session.query(AlertInstance)
                .filter(
                    AlertInstance.rule_id == rule.get("id"),
                    AlertInstance.dedup_key == dedup_key,
                    AlertInstance.state.in_(ACTIVE_STATES),
                )
                .order_by(AlertInstance.triggered_at.desc())
                .first()
            )
            if existing is not None:
                om.ALERTS_DEDUPLICATED_TOTAL.inc()
                return existing.id

            now = _utcnow()
            expires_at = None
            expiry_seconds = (rule.get("extra") or {}).get("expiry_seconds")
            if expiry_seconds:
                expires_at = now + timedelta(seconds=int(expiry_seconds))

            message = rule.get("message") or (
                f"Rule '{rule.get('name')}' matched on stream {stream_id}"
            )
            instance = AlertInstance(
                rule_id=rule.get("id"),
                stream_id=stream_id,
                state="triggered",
                severity=rule.get("severity", "warning"),
                priority=rule.get("priority", 0),
                dedup_key=dedup_key,
                message=message,
                details={"computed": computed, "event": event},
                triggered_at=now,
                expires_at=expires_at,
            )
            session.add(instance)
            session.flush()
            session.add(
                AlertStateHistory(
                    alert_instance_id=instance.id,
                    from_state=None,
                    to_state="triggered",
                    changed_at=now,
                )
            )
            session.commit()
            alert_id = instance.id
            severity = instance.severity
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.warning(f"Alert trigger failed for stream {stream_id}: {exc}")
            return None
        finally:
            session.close()

        om.ALERTS_TRIGGERED_TOTAL.inc()
        self._publisher.publish_lifecycle(stream_id, alert_id, "triggered", severity, {"computed": computed})
        self._enqueue_notification(
            {
                "alert_id": alert_id,
                "rule_id": rule.get("id"),
                "stream_id": stream_id,
                "severity": severity,
                "priority": rule.get("priority", 0),
                "state": "triggered",
                "message": message,
                "details": {"computed": computed},
                "triggered_at": now.isoformat(),
            }
        )
        return alert_id

    def acknowledge(self, alert_id: int, user: Optional[str], note: Optional[str] = None) -> Dict[str, Any]:
        return self._transition(
            alert_id,
            allowed_from=("triggered",),
            to_state="acknowledged",
            user=user,
            note=note,
            metric=om.ALERTS_ACKNOWLEDGED_TOTAL,
            set_fields=lambda inst, now: (
                setattr(inst, "acknowledged_at", now),
                setattr(inst, "acknowledged_by", user),
            ),
        )

    def resolve(self, alert_id: int, user: Optional[str], note: Optional[str] = None) -> Dict[str, Any]:
        return self._transition(
            alert_id,
            allowed_from=("triggered", "acknowledged", "suppressed"),
            to_state="resolved",
            user=user,
            note=note,
            metric=om.ALERTS_RESOLVED_TOTAL,
            set_fields=lambda inst, now: setattr(inst, "resolved_at", now),
        )

    def suppress(self, alert_id: int, user: Optional[str], seconds: int, note: Optional[str] = None) -> Dict[str, Any]:
        return self._transition(
            alert_id,
            allowed_from=("triggered", "acknowledged"),
            to_state="suppressed",
            user=user,
            note=note,
            metric=None,
            set_fields=lambda inst, now: setattr(
                inst, "suppressed_until", now + timedelta(seconds=seconds)
            ),
        )

    def _transition(self, alert_id, allowed_from, to_state, user, note, metric, set_fields) -> Dict[str, Any]:
        session = self._session_factory()
        try:
            instance = session.query(AlertInstance).filter(AlertInstance.id == alert_id).first()
            if instance is None:
                raise AlertTransitionError("alert not found")
            if instance.state not in allowed_from:
                raise AlertTransitionError(
                    f"cannot transition from {instance.state} to {to_state}"
                )
            now = _utcnow()
            from_state = instance.state
            instance.state = to_state
            set_fields(instance, now)
            session.add(
                AlertStateHistory(
                    alert_instance_id=instance.id,
                    from_state=from_state,
                    to_state=to_state,
                    changed_at=now,
                    changed_by=user,
                    note=note,
                )
            )
            session.commit()
            result = {
                "id": instance.id,
                "stream_id": instance.stream_id,
                "state": to_state,
                "severity": instance.severity,
            }
        except AlertTransitionError:
            session.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            raise AlertTransitionError(str(exc))
        finally:
            session.close()

        if metric is not None:
            metric.inc()
        self._publisher.publish_lifecycle(
            result["stream_id"], result["id"], to_state, result["severity"], {}
        )
        return result

    def run_expiry(self) -> int:
        session = self._session_factory()
        expired_ids: List[int] = []
        try:
            now = _utcnow()
            rows = (
                session.query(AlertInstance)
                .filter(
                    AlertInstance.state.in_(("triggered", "acknowledged")),
                    AlertInstance.expires_at != None,  # noqa: E711
                    AlertInstance.expires_at < now,
                )
                .all()
            )
            for row in rows:
                from_state = row.state
                row.state = "expired"
                session.add(
                    AlertStateHistory(
                        alert_instance_id=row.id,
                        from_state=from_state,
                        to_state="expired",
                        changed_at=now,
                    )
                )
                expired_ids.append(row.id)
            if rows:
                session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.warning(f"Alert expiry pass failed: {exc}")
        finally:
            session.close()
        return len(expired_ids)

    def _enqueue_notification(self, payload: Dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            logger.warning("Alert notification queue full, dropping notification job")

    def _worker_loop(self) -> None:
        last_expiry = 0.0
        while self._running:
            try:
                payload = self._queue.get(timeout=1.0)
            except queue.Empty:
                payload = None
            if payload is not None:
                self._process_notification(payload)
            now = time.time()
            if now - last_expiry >= self._expiry_interval:
                last_expiry = now
                try:
                    self.run_expiry()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Expiry pass error: {exc}")

    def _drain(self) -> None:
        while True:
            try:
                payload = self._queue.get_nowait()
            except queue.Empty:
                break
            self._process_notification(payload)

    def _process_notification(self, payload: Dict[str, Any]) -> None:
        alert_id = payload.get("alert_id")
        try:
            self._dispatcher.dispatch(alert_id, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Notification dispatch error for alert {alert_id}: {exc}")
        self._mark_notified(alert_id)

    def _mark_notified(self, alert_id: Any) -> None:
        session = self._session_factory()
        try:
            instance = session.query(AlertInstance).filter(AlertInstance.id == alert_id).first()
            if instance is not None:
                instance.last_notified_at = _utcnow()
                instance.notification_count = (instance.notification_count or 0) + 1
                session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.warning(f"Failed marking alert {alert_id} notified: {exc}")
        finally:
            session.close()
