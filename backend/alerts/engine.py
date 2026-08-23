from __future__ import annotations

import queue
import threading
import time
from typing import Any, Dict, List, Optional

from ..observability import metrics as om
from ..observability.logging import get_logger
from .manager import AlertManager
from .models import AlertRule
from .rules import evaluate_rule
from .state import AlertStateStore

logger = get_logger(__name__, component="backend.alerts")


class AlertRuleEngine:
    def __init__(
        self,
        session_factory,
        analytics,
        manager: AlertManager,
        state_store: AlertStateStore,
        *,
        default_cooldown_seconds: int = 60,
        dedup_window_seconds: int = 300,
        rule_cache_ttl_seconds: int = 10,
        queue_capacity: int = 1000,
    ) -> None:
        self._session_factory = session_factory
        self._analytics = analytics
        self._manager = manager
        self._state = state_store
        self._default_cooldown = default_cooldown_seconds
        self._dedup_window = dedup_window_seconds
        self._rule_cache_ttl = rule_cache_ttl_seconds

        self._cache_lock = threading.Lock()
        self._rule_cache: Dict[Optional[str], List[Dict[str, Any]]] = {}
        self._cache_ts: Dict[Optional[str], float] = {}

        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=queue_capacity)
        self._running = False
        self._worker: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        logger.info("Alert rule engine started")

    def stop(self) -> None:
        self._running = False
        if self._worker is not None:
            self._worker.join(timeout=5)
        self._drain()

    def handle_event(self, record: dict) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            logger.warning("Alert engine queue full, dropping event")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Alert engine handle_event error: {exc}")

    def invalidate_cache(self) -> None:
        with self._cache_lock:
            self._rule_cache.clear()
            self._cache_ts.clear()

    def reload_rules(self) -> None:
        self.invalidate_cache()

    def _worker_loop(self) -> None:
        while self._running:
            try:
                record = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._evaluate(record)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Alert evaluation error: {exc}")

    def _drain(self) -> None:
        while True:
            try:
                record = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._evaluate(record)
            except Exception:  # noqa: BLE001
                pass

    def _evaluate(self, record: Dict[str, Any]) -> None:
        stream_id = record.get("stream_id")
        if not stream_id:
            return
        snapshot: Dict[str, Any] = {}
        if self._analytics is not None:
            try:
                snapshot = self._analytics.get_current(stream_id) or {}
            except Exception:  # noqa: BLE001
                snapshot = {}

        rules = self._get_rules(stream_id)
        for rule in rules:
            start = time.perf_counter()
            try:
                result = evaluate_rule(
                    rule.get("conditions", []),
                    rule.get("condition_logic", "AND"),
                    snapshot,
                    record,
                )
            finally:
                om.ALERT_RULE_EVALUATION_LATENCY.observe(
                    max(time.perf_counter() - start, 0.0)
                )
            om.ALERT_RULES_EVALUATED_TOTAL.inc()

            if not result["matched"]:
                continue

            dedup_key = self._dedup_key(rule, stream_id, record)
            if self._state.in_cooldown(rule["id"], dedup_key):
                continue
            if self._state.is_duplicate(rule["id"], dedup_key):
                continue

            rule_payload = dict(rule)
            rule_payload["dedup_key"] = dedup_key
            alert_id = self._manager.trigger(
                rule_payload, result["computed_values"], stream_id, record
            )
            if alert_id is not None:
                self._state.set_cooldown(
                    rule["id"], dedup_key, rule.get("cooldown_seconds", self._default_cooldown)
                )
                self._state.mark_seen(rule["id"], dedup_key, self._dedup_window)

    def _dedup_key(self, rule: Dict[str, Any], stream_id: str, record: Dict[str, Any]) -> str:
        template = rule.get("dedup_key_template")
        if template:
            try:
                return template.format(
                    stream_id=stream_id,
                    rule_id=rule.get("id"),
                    event_type=record.get("event_type"),
                    class_name=record.get("class_name"),
                )
            except Exception:  # noqa: BLE001
                pass
        return f"{rule.get('id')}:{stream_id}"

    def _get_rules(self, stream_id: str) -> List[Dict[str, Any]]:
        now = time.time()
        with self._cache_lock:
            cached = self._rule_cache.get(stream_id)
            ts = self._cache_ts.get(stream_id, 0.0)
            if cached is not None and now - ts < self._rule_cache_ttl:
                return cached
        rules = self._load_rules(stream_id)
        with self._cache_lock:
            self._rule_cache[stream_id] = rules
            self._cache_ts[stream_id] = now
        return rules

    def _load_rules(self, stream_id: str) -> List[Dict[str, Any]]:
        session = self._session_factory()
        try:
            rows = (
                session.query(AlertRule)
                .filter(
                    AlertRule.enabled == True,  # noqa: E712
                    (AlertRule.stream_id == stream_id) | (AlertRule.stream_id == None),  # noqa: E711
                )
                .order_by(AlertRule.priority.desc())
                .all()
            )
            return [self._row_to_dict(row) for row in rows]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed loading alert rules for {stream_id}: {exc}")
            return []
        finally:
            session.close()

    @staticmethod
    def _row_to_dict(row: AlertRule) -> Dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "stream_id": row.stream_id,
            "severity": row.severity,
            "priority": row.priority,
            "cooldown_seconds": row.cooldown_seconds,
            "conditions": row.conditions or [],
            "condition_logic": row.condition_logic,
            "dedup_key_template": row.dedup_key_template,
            "extra": row.extra or {},
        }
