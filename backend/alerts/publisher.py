from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..messaging.events import KafkaEventPublisher


class AlertEventPublisher:
    def __init__(self, base_publisher: Optional[KafkaEventPublisher], topic: str) -> None:
        self._base = base_publisher
        self._topic = topic

    @property
    def enabled(self) -> bool:
        return self._base is not None and self._base.enabled

    def publish_lifecycle(
        self,
        stream_id: str,
        alert_id: Any,
        state: str,
        severity: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not self.enabled:
            return False
        record = {
            "type": "alert_lifecycle",
            "stream_id": stream_id,
            "alert_id": alert_id,
            "state": state,
            "severity": severity,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data or {},
        }
        producer = self._base._producer
        return producer is not None and producer.publish(
            self._topic, key=str(stream_id), value=record
        )
