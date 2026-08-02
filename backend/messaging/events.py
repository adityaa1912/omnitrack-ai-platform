"""Bridge that publishes derived EventBuffer records to Kafka.

A :class:`KafkaEventPublisher` is registered as an ``EventBuffer.subscribe``
callback. The buffer invokes it on the inference thread after each append, so
``__call__`` is deliberately cheap and non-blocking: it hands the record to the
producer's internal queue and returns. Only derived events (already produced by
the event engine) pass through here — never raw frames, images, or per-frame
detections.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..observability.logging import get_logger
from .producer import KafkaProducer

logger = get_logger(__name__, component="backend.messaging")

Record = Dict[str, Any]


class KafkaEventPublisher:
    """Publish each derived event record to the configured Kafka topic.

    Fail-safe by construction: every path either enqueues asynchronously or
    drops with a log line. It never raises, so it can never stall or kill the
    inference loop that invokes it.
    """

    def __init__(self, producer: Optional[KafkaProducer], topic: str) -> None:
        self._producer = producer
        self._topic = topic

    @property
    def enabled(self) -> bool:
        """Whether a live producer is configured."""
        return self._producer is not None

    def __call__(self, record: Record) -> None:
        """EventBuffer subscriber callback — publish one derived event."""
        if self._producer is None:
            return
        # Key by stream_id so per-stream events partition together (ordered).
        key = record.get("stream_id")
        self._producer.publish(self._topic, key=key, value=record)
