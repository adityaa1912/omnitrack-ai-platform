"""Re-export analytics metrics from the observability module.

Analytics metrics are defined in ``backend.observability.metrics`` so they
share a single Prometheus registry. This module exists as a convenience
for callers that only import the analytics package.
"""

from backend.observability.metrics import (
    ANALYTICS_AGGREGATOR_STREAMS,
    ANALYTICS_CACHE_HITS_TOTAL,
    ANALYTICS_CACHE_MISSES_TOTAL,
    ANALYTICS_EVENTS_TOTAL,
    ANALYTICS_FLUSHES_TOTAL,
    ANALYTICS_LINE_CROSSINGS,
    ANALYTICS_OBJECT_COUNT,
    ANALYTICS_QUERY_LATENCY,
    ANALYTICS_RETENTION_DELETES_TOTAL,
    ANALYTICS_SUMMARIES_FLUSHED,
    ANALYTICS_ZONE_OCCUPANCY,
)

__all__ = [
    "ANALYTICS_AGGREGATOR_STREAMS",
    "ANALYTICS_CACHE_HITS_TOTAL",
    "ANALYTICS_CACHE_MISSES_TOTAL",
    "ANALYTICS_EVENTS_TOTAL",
    "ANALYTICS_FLUSHES_TOTAL",
    "ANALYTICS_LINE_CROSSINGS",
    "ANALYTICS_OBJECT_COUNT",
    "ANALYTICS_QUERY_LATENCY",
    "ANALYTICS_RETENTION_DELETES_TOTAL",
    "ANALYTICS_SUMMARIES_FLUSHED",
    "ANALYTICS_ZONE_OCCUPANCY",
]
