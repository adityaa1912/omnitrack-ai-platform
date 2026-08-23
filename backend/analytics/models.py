"""SQLAlchemy models for analytics historical persistence."""

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, Float, JSON, Index
from backend.models import Base


class AnalyticsSummary(Base):
    """Aggregated analytics snapshot persisted to PostgreSQL.

    Each row captures a time window of analytics for one stream, broken
    down by class where applicable. Used for historical queries — real-time
    reads come from the in-memory ``AnalyticsAggregator`` and are cached
    in Redis when available.
    """

    __tablename__ = "analytics_summaries"

    id = Column(Integer, primary_key=True)
    stream_id = Column(String, index=True, nullable=False)
    time_window_start = Column(DateTime, nullable=False, index=True)
    time_window_end = Column(DateTime, nullable=False, index=True)
    class_name = Column(String, nullable=True, index=True)
    object_count = Column(Integer, default=0, nullable=False)
    unique_tracks = Column(Integer, default=0, nullable=False)
    zone_entry_count = Column(Integer, default=0, nullable=False)
    zone_exit_count = Column(Integer, default=0, nullable=False)
    zone_occupancy_seconds = Column(Float, default=0.0, nullable=False)
    line_crossing_positive_count = Column(Integer, default=0, nullable=False)
    line_crossing_negative_count = Column(Integer, default=0, nullable=False)
    dwell_time_total_seconds = Column(Float, default=0.0, nullable=False)
    stationary_events = Column(Integer, default=0, nullable=False)
    near_collision_events = Column(Integer, default=0, nullable=False)
    extra = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_analytics_summaries_stream_window", "stream_id", "time_window_start"),
        Index("ix_analytics_summaries_class", "stream_id", "class_name", "time_window_start"),
    )


class ZoneOccupancy(Base):
    """Per-zone occupancy records for historical queries.

    Stores the count of unique tracks present in each zone per time
    window, along with total seconds each track spent inside the zone.
    """

    __tablename__ = "zone_occupancy"

    id = Column(Integer, primary_key=True)
    stream_id = Column(String, index=True, nullable=False)
    zone_name = Column(String, index=True, nullable=False)
    time_window_start = Column(DateTime, nullable=False, index=True)
    time_window_end = Column(DateTime, nullable=False, index=True)
    entry_count = Column(Integer, default=0, nullable=False)
    exit_count = Column(Integer, default=0, nullable=False)
    unique_tracks = Column(Integer, default=0, nullable=False)
    total_occupancy_seconds = Column(Float, default=0.0, nullable=False)
    max_concurrent_tracks = Column(Integer, default=0, nullable=False)
    extra = Column(JSON, nullable=True)

    __table_args__ = (
        Index(
            "ix_zone_occupancy_stream_zone_window",
            "stream_id",
            "zone_name",
            "time_window_start",
        ),
    )


class LineCrossing(Base):
    """Per-line crossing records for historical queries.

    Stores directional crossing counts per line per time window.
    """

    __tablename__ = "line_crossings"

    id = Column(Integer, primary_key=True)
    stream_id = Column(String, index=True, nullable=False)
    line_name = Column(String, index=True, nullable=False)
    time_window_start = Column(DateTime, nullable=False, index=True)
    time_window_end = Column(DateTime, nullable=False, index=True)
    positive_count = Column(Integer, default=0, nullable=False)
    negative_count = Column(Integer, default=0, nullable=False)
    unique_tracks = Column(Integer, default=0, nullable=False)
    extra = Column(JSON, nullable=True)

    __table_args__ = (
        Index(
            "ix_line_crossings_stream_line_window",
            "stream_id",
            "line_name",
            "time_window_start",
        ),
    )


class TrajectorySnapshot(Base):
    """Trajectory data stored for individual tracks.

    Captures trajectory points for a single track at the moment the track
    is lost or the window expires, so historical trajectory analysis is
    possible without streaming every point.
    """

    __tablename__ = "trajectory_snapshots"

    id = Column(Integer, primary_key=True)
    stream_id = Column(String, index=True, nullable=False)
    track_id = Column(Integer, index=True, nullable=False)
    class_name = Column(String, nullable=True)
    time_window_start = Column(DateTime, nullable=False, index=True)
    time_window_end = Column(DateTime, nullable=False, index=True)
    first_seen_frame = Column(Integer, default=0, nullable=False)
    last_seen_frame = Column(Integer, default=0, nullable=False)
    points = Column(JSON, nullable=True)
    total_distance_meters = Column(Float, default=0.0, nullable=False)
    avg_speed_mps = Column(Float, default=0.0, nullable=False)
    extra = Column(JSON, nullable=True)

    __table_args__ = (
        Index(
            "ix_trajectory_snapshots_stream_track",
            "stream_id",
            "track_id",
        ),
        Index(
            "ix_trajectory_snapshots_stream_window",
            "stream_id",
            "time_window_start",
        ),
    )
