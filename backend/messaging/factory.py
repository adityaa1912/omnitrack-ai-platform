"""Factory for the optional Kafka producer.

Returns ``None`` when Kafka is disabled (no bootstrap servers resolved), so the
rest of the application treats "no producer" as "event bus off" without a
separate flag check. Construction is lazy — it does not block on the broker.
"""

from __future__ import annotations

from typing import Optional

from ..observability.logging import get_logger
from .producer import KafkaProducer

logger = get_logger(__name__, component="backend.messaging")


def create_kafka_producer(
    bootstrap_servers: Optional[str],
    *,
    retries: int = 3,
    delivery_timeout_seconds: float = 5.0,
) -> Optional[KafkaProducer]:
    """Create a :class:`KafkaProducer`, or ``None`` when Kafka is disabled.

    ``bootstrap_servers`` should come from
    ``Settings.resolved_kafka_bootstrap_servers`` — already ``None`` when the
    feature flag is off. Returns ``None`` (and logs) if the client cannot be
    constructed, so a misconfiguration never prevents startup.
    """
    if not bootstrap_servers:
        return None
    try:
        producer = KafkaProducer(
            bootstrap_servers,
            retries=retries,
            delivery_timeout_seconds=delivery_timeout_seconds,
        )
        logger.info(
            f"Kafka producer initialized for {bootstrap_servers}",
            extra={"fields": {"bootstrap_servers": bootstrap_servers}},
        )
        return producer
    except Exception as exc:  # noqa: BLE001 - Kafka must be optional
        logger.warning(f"Kafka producer unavailable, event bus disabled: {exc}")
        return None
