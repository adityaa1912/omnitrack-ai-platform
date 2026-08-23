"""Analytics engine for OmniTrack.

Provides real-time in-memory aggregation of derived inference events,
historical persistence to PostgreSQL, Redis caching for hot reads,
Kafka publishing of analytics snapshots, and a REST API for querying
both current and historical analytics.

Architecture
------------
The analytics engine hooks into the existing ``EventBuffer`` via
``EventBuffer.subscribe`` — events are delivered on the inference thread
and consumed off the hot path. An ``AnalyticsAggregator`` maintains
per-stream counters (object counts, zone occupancy, line crossings,
dwell time) in memory, writes periodic snapshots to PostgreSQL, and
publishes them to Kafka when the bus is enabled.

REST endpoints live in ``router.py`` and are protected by the existing
RBAC layer (viewer reads, operator/admin manages config).
"""
