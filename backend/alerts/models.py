from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    Float,
    Boolean,
    JSON,
    Text,
    ForeignKey,
    Index,
)

from backend.models import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    stream_id = Column(String, nullable=True, index=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    rule_type = Column(String(64), nullable=True)
    conditions = Column(JSON, nullable=False, default=list)
    condition_logic = Column(String(8), nullable=False, default="AND")
    severity = Column(String(32), nullable=False, default="warning")
    priority = Column(Integer, nullable=False, default=0, index=True)
    cooldown_seconds = Column(Integer, nullable=False, default=60)
    dedup_key_template = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    extra = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_alert_rules_stream_enabled", "stream_id", "enabled"),
        Index("ix_alert_rules_priority", "priority"),
    )


class AlertInstance(Base):
    __tablename__ = "alert_instances"

    id = Column(Integer, primary_key=True)
    rule_id = Column(
        Integer, ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=True, index=True
    )
    stream_id = Column(String, nullable=False, index=True)
    state = Column(String(32), nullable=False, default="triggered", index=True)
    severity = Column(String(32), nullable=False, default="warning", index=True)
    priority = Column(Integer, nullable=False, default=0)
    dedup_key = Column(String(255), nullable=True, index=True)
    message = Column(Text, nullable=True)
    details = Column(JSON, nullable=True)
    triggered_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    suppressed_until = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    acknowledged_by = Column(String(255), nullable=True)
    last_notified_at = Column(DateTime(timezone=True), nullable=True)
    notification_count = Column(Integer, nullable=False, default=0)
    extra = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_alert_instances_stream_state", "stream_id", "state"),
        Index("ix_alert_instances_rule_state_dedup", "rule_id", "state", "dedup_key"),
    )


class AlertStateHistory(Base):
    __tablename__ = "alert_state_history"

    id = Column(Integer, primary_key=True)
    alert_instance_id = Column(
        Integer, ForeignKey("alert_instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_state = Column(String(32), nullable=True)
    to_state = Column(String(32), nullable=False)
    changed_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    changed_by = Column(String(255), nullable=True)
    note = Column(String(512), nullable=True)
    extra = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_alert_state_history_instance", "alert_instance_id", "changed_at"),
    )


class NotificationAttempt(Base):
    __tablename__ = "notification_attempts"

    id = Column(Integer, primary_key=True)
    alert_instance_id = Column(
        Integer, ForeignKey("alert_instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False, default=1)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    payload = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_notification_attempts_instance_provider", "alert_instance_id", "provider"),
    )
