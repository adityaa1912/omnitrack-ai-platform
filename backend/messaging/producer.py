"""Kafka producer for derived inference events.

Wraps a ``confluent_kafka.Producer`` (librdkafka) with a lazy, non-blocking
``publish`` API. Producing is asynchronous: ``produce()`` enqueues the record
on librdkafka's internal queue and returns immediately, so a slow or unreachable
broker never blocks the calling (inference) thread. Delivery failures are
reported on a callback and logged — they are never raised to the caller, so a
Kafka outage can never stop inference.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Optional

from ..observability.logging import get_logger

logger = get_logger(__name__, component="backend.messaging")


class KafkaProducer:
    """A lazily-connected, fail-safe Kafka producer for event records.

    The underlying librdkafka producer connects in the background; ``publish``
    never blocks on the network. When the broker is down, records queue up to
    librdkafka's buffer and are then dropped (with a logged delivery error)
    rather than applying back-pressure to inference.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        *,
        retries: int = 3,
        delivery_timeout_seconds: float = 5.0,
    ) -> None:
        # Imported lazily so the module can be imported (and the app started)
        # even when the confluent_kafka package/broker is unavailable.
        from confluent_kafka import Producer

        self._bootstrap_servers = bootstrap_servers
        self._lock = threading.Lock()
        self._closed = False
        # delivery.timeout.ms bounds total time (queue + retries) before a
        # message is failed; retries controls the per-request retry count.
        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                "retries": retries,
                "delivery.timeout.ms": int(delivery_timeout_seconds * 1000),
                # Bound the internal queue so a down broker cannot grow memory
                # without limit; overflow surfaces as a logged BufferError.
                "queue.buffering.max.messages": 100000,
            }
        )

    @property
    def bootstrap_servers(self) -> str:
        return self._bootstrap_servers

    def _on_delivery(self, err, _msg) -> None:
        """Delivery report callback (runs on a librdkafka thread)."""
        if err is not None:
            logger.warning(f"Kafka delivery failed: {err}")

    def publish(self, topic: str, key: Optional[str], value: Any) -> bool:
        """Enqueue one JSON record for async delivery. Returns True if queued.

        Never raises for broker/delivery problems. Returns False (and logs) when
        the producer is closed, the payload cannot be serialized, or the local
        queue is full — in every case the caller continues unaffected.
        """
        if self._closed:
            return False
        try:
            payload = json.dumps(value).encode("utf-8")
        except (TypeError, ValueError) as exc:
            logger.warning(f"Kafka payload not serializable, dropping: {exc}")
            return False
        try:
            self._producer.produce(
                topic,
                key=(key.encode("utf-8") if key is not None else None),
                value=payload,
                on_delivery=self._on_delivery,
            )
            # Trigger delivery callbacks for completed sends without blocking.
            self._producer.poll(0)
            return True
        except BufferError:
            logger.warning("Kafka local queue full, dropping event")
            return False
        except Exception as exc:  # noqa: BLE001 - publishing must never raise
            logger.warning(f"Kafka produce failed, dropping event: {exc}")
            return False

    def ping(self) -> bool:
        """Best-effort broker reachability check for readiness.

        Uses ``list_topics`` with a short timeout; returns True when the broker
        answers. Never raises.
        """
        if self._closed:
            return False
        try:
            self._producer.list_topics(timeout=2.0)
            return True
        except Exception as exc:  # noqa: BLE001 - a failing check is data
            logger.warning(f"Kafka health check failed: {exc}")
            return False

    def close(self, timeout_seconds: float = 5.0) -> None:
        """Flush outstanding messages and close. Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            # flush() blocks up to the timeout to deliver queued messages.
            self._producer.flush(timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - shutdown must not raise
            logger.warning(f"Kafka flush on close failed: {exc}")
