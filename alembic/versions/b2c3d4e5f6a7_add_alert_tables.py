"""Add alert engine tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-23 13:30:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("stream_id", sa.String(), nullable=True, index=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, default=True, index=True),
        sa.Column("rule_type", sa.String(length=64), nullable=True),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("condition_logic", sa.String(length=8), nullable=False, default="AND"),
        sa.Column("severity", sa.String(length=32), nullable=False, default="warning"),
        sa.Column("priority", sa.Integer(), nullable=False, default=0, index=True),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False, default=60),
        sa.Column("dedup_key_template", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Index("ix_alert_rules_stream_enabled", "stream_id", "enabled"),
        sa.Index("ix_alert_rules_priority", "priority"),
    )

    op.create_table(
        "alert_instances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "rule_id",
            sa.Integer(),
            sa.ForeignKey("alert_rules.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("stream_id", sa.String(), nullable=False, index=True),
        sa.Column("state", sa.String(length=32), nullable=False, default="triggered", index=True),
        sa.Column("severity", sa.String(length=32), nullable=False, default="warning", index=True),
        sa.Column("priority", sa.Integer(), nullable=False, default=0),
        sa.Column("dedup_key", sa.String(length=255), nullable=True, index=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suppressed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("acknowledged_by", sa.String(length=255), nullable=True),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notification_count", sa.Integer(), nullable=False, default=0),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Index("ix_alert_instances_stream_state", "stream_id", "state"),
        sa.Index("ix_alert_instances_rule_state_dedup", "rule_id", "state", "dedup_key"),
    )

    op.create_table(
        "alert_state_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "alert_instance_id",
            sa.Integer(),
            sa.ForeignKey("alert_instances.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("from_state", sa.String(length=32), nullable=True),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("changed_by", sa.String(length=255), nullable=True),
        sa.Column("note", sa.String(length=512), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Index("ix_alert_state_history_instance", "alert_instance_id", "changed_at"),
    )

    op.create_table(
        "notification_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "alert_instance_id",
            sa.Integer(),
            sa.ForeignKey("alert_instances.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False, index=True),
        sa.Column("status", sa.String(length=32), nullable=False, index=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False, default=1),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Index("ix_notification_attempts_instance_provider", "alert_instance_id", "provider"),
    )


def downgrade() -> None:
    op.drop_table("notification_attempts")
    op.drop_table("alert_state_history")
    op.drop_table("alert_instances")
    op.drop_table("alert_rules")
