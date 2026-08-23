"""Kafka publisher for analytics events.

Publishes analytics snapshots to Kafka via the existing
``KafkaEventPublisher`` infrastructure. When Kafka is disabled the
publisher is a no-op and never blocks the inference path.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..messaging.events import KafkaEventPublisher


class AnalyticsEventPublisher:
    """Publishes analytics snapshots to Kafka."""

    def __init__(
        self,
        base_publisher: Optional[KafkaEventPublisher],
        topic: str,
    ) -> None:
        self._base = base_publisher
        self._topic = topic

    @property
    def enabled(self) -> bool:
        return self._base is not None and self._base.enabled

    def publish(self, stream_id: str, data: Dict[str, Any]) -> bool:
        """Publish one analytics snapshot. Returns True if enqueued."""
        if not self.enabled:
            return False
        record = {
            "type": "analytics_snapshot",
            "stream_id": stream_id,
            "timestamp": data.get("timestamp"),
            "data": data,
        }
        return self._base._producer is not None and self._base._producer.publish(
            self._topic, key=stream_id, value=record
        )
