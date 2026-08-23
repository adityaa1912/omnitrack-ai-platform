"""Add analytics tables

Revision ID: a1b2c3d4e5f6
Revises: 67e636f9b87d
Create Date: 2026-08-22 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "67e636f9b87d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema – create analytics tables."""
    op.create_table(
        "analytics_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stream_id", sa.String(), nullable=False, index=True),
        sa.Column("time_window_start", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("time_window_end", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("class_name", sa.String(), nullable=True, index=True),
        sa.Column("object_count", sa.Integer(), nullable=False, default=0),
        sa.Column("unique_tracks", sa.Integer(), nullable=False, default=0),
        sa.Column("zone_entry_count", sa.Integer(), nullable=False, default=0),
        sa.Column("zone_exit_count", sa.Integer(), nullable=False, default=0),
        sa.Column("zone_occupancy_seconds", sa.Float(), nullable=False, default=0.0),
        sa.Column("line_crossing_positive_count", sa.Integer(), nullable=False, default=0),
        sa.Column("line_crossing_negative_count", sa.Integer(), nullable=False, default=0),
        sa.Column("dwell_time_total_seconds", sa.Float(), nullable=False, default=0.0),
        sa.Column("stationary_events", sa.Integer(), nullable=False, default=0),
        sa.Column("near_collision_events", sa.Integer(), nullable=False, default=0),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Index("ix_analytics_summaries_stream_window", "stream_id", "time_window_start"),
        sa.Index("ix_analytics_summaries_class", "stream_id", "class_name", "time_window_start"),
    )

    op.create_table(
        "zone_occupancy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stream_id", sa.String(), nullable=False, index=True),
        sa.Column("zone_name", sa.String(), nullable=False, index=True),
        sa.Column("time_window_start", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("time_window_end", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("entry_count", sa.Integer(), nullable=False, default=0),
        sa.Column("exit_count", sa.Integer(), nullable=False, default=0),
        sa.Column("unique_tracks", sa.Integer(), nullable=False, default=0),
        sa.Column("total_occupancy_seconds", sa.Float(), nullable=False, default=0.0),
        sa.Column("max_concurrent_tracks", sa.Integer(), nullable=False, default=0),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Index("ix_zone_occupancy_stream_zone_window", "stream_id", "zone_name", "time_window_start"),
    )

    op.create_table(
        "line_crossings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stream_id", sa.String(), nullable=False, index=True),
        sa.Column("line_name", sa.String(), nullable=False, index=True),
        sa.Column("time_window_start", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("time_window_end", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("positive_count", sa.Integer(), nullable=False, default=0),
        sa.Column("negative_count", sa.Integer(), nullable=False, default=0),
        sa.Column("unique_tracks", sa.Integer(), nullable=False, default=0),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Index("ix_line_crossings_stream_line_window", "stream_id", "line_name", "time_window_start"),
    )

    op.create_table(
        "trajectory_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stream_id", sa.String(), nullable=False, index=True),
        sa.Column("track_id", sa.Integer(), nullable=False, index=True),
        sa.Column("class_name", sa.String(), nullable=True),
        sa.Column("time_window_start", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("time_window_end", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("first_seen_frame", sa.Integer(), nullable=False, default=0),
        sa.Column("last_seen_frame", sa.Integer(), nullable=False, default=0),
        sa.Column("points", sa.JSON(), nullable=True),
        sa.Column("total_distance_meters", sa.Float(), nullable=False, default=0.0),
        sa.Column("avg_speed_mps", sa.Float(), nullable=False, default=0.0),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Index("ix_trajectory_snapshots_stream_track", "stream_id", "track_id"),
        sa.Index("ix_trajectory_snapshots_stream_window", "stream_id", "time_window_start"),
    )


def downgrade() -> None:
    """Downgrade schema – drop analytics tables."""
    op.drop_table("trajectory_snapshots")
    op.drop_table("line_crossings")
    op.drop_table("zone_occupancy")
    op.drop_table("analytics_summaries")
