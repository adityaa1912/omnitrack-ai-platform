"""Optional Kafka event bus integration for the OmniTrack backend."""

from .producer import KafkaProducer
from .factory import create_kafka_producer
from .events import KafkaEventPublisher

__all__ = ["KafkaProducer", "create_kafka_producer", "KafkaEventPublisher"]
