"""
Database models for persistence of inference data.

Stores detections, tracking data, and metrics for historical analysis.
Uses SQLAlchemy ORM for clean database abstraction.
"""

from datetime import datetime
from typing import Tuple

from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    DateTime,
    Boolean,
    JSON,
    Text,
    ForeignKey,
    Index,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker, relationship

Base = declarative_base()


class Detection(Base):
    """Persisted detection record."""
    __tablename__ = "detections"

    id = Column(Integer, primary_key=True)
    frame_id = Column(Integer, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # Bounding box coordinates
    x1 = Column(Float)
    y1 = Column(Float)
    x2 = Column(Float)
    y2 = Column(Float)

    # Detection info
    class_id = Column(Integer)
    class_name = Column(String)
    confidence = Column(Float)

    # Tracking info
    track_id = Column(Integer, nullable=True, index=True)

    # Metadata
    stream_id = Column(String, index=True)
    inference_time_ms = Column(Float)


class Metric(Base):
    """Persisted inference metrics."""
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    stream_id = Column(String, index=True)

    # Performance metrics
    inference_time_ms = Column(Float)
    fps = Column(Float)
    num_detections = Column(Integer)

    # System metrics
    cpu_percent = Column(Float, nullable=True)
    memory_mb = Column(Float, nullable=True)


class StreamSession(Base):
    """Tracks active and historical stream sessions."""
    __tablename__ = "stream_sessions"

    id = Column(Integer, primary_key=True)
    stream_id = Column(String, unique=True, index=True)
    source = Column(String)  # webcam, file path, or RTSP URL

    started_at = Column(DateTime, default=datetime.utcnow, index=True)
    ended_at = Column(DateTime, nullable=True)

    # Configuration
    width = Column(Integer)
    height = Column(Integer)
    tracking_enabled = Column(Boolean, default=True)

    # Stats
    total_frames = Column(Integer, default=0)
    total_detections = Column(Integer, default=0)

    # Status
    is_active = Column(Boolean, default=True, index=True)
    error_message = Column(String, nullable=True)


class User(Base):
    """User accounts for authentication and authorization."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="viewer", index=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)

    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="user")

    __table_args__ = (
        Index("ix_users_role_is_active", "role", "is_active"),
    )


class APIKey(Base):
    """API keys for programmatic access."""
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    key_hash = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="api_keys")

    __table_args__ = (
        Index("ix_api_keys_user_active", "user_id", "is_active"),
    )


class AuditLog(Base):
    """Audit trail for security-relevant actions."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    username = Column(String(255), nullable=True)
    action = Column(String(255), nullable=False, index=True)
    resource_type = Column(String(100), nullable=True, index=True)
    resource_id = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False, index=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    details = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    user = relationship("User", back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_logs_user_action", "user_id", "action"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_created_status", "created_at", "status"),
    )


class Session(Base):
    """Active user sessions for token revocation and session management."""
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_jti = Column(String(255), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    last_activity_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_sessions_user_active", "user_id", "is_active"),
        Index("ix_sessions_expires", "expires_at", "is_active"),
    )


def create_session_factory(
    db_path: str = "inference_data.db",
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_recycle_seconds: int = 1800,
    pool_pre_ping: bool = True,
) -> Tuple[Engine, scoped_session]:
    """
    Build a thread-safe session factory for the configured database.

    ``db_path`` accepts either a bare SQLite file path (legacy/dev convenience)
    or a full SQLAlchemy URL (e.g. ``postgresql+psycopg2://...``). The dialect
    is detected from the resolved URL.

    Returns ``(engine, Session)`` where ``Session`` is a ``scoped_session``
    registry. Calling ``Session()`` returns the *current thread's* Session — the
    only supported way to use SQLAlchemy across threads, since a ``Session`` must
    never be shared between threads. Every thread that uses the registry MUST
    call ``Session.remove()`` when it is done so its thread-local session (and
    the connection it holds) is released.

    SQLite specifics that make concurrent inference threads safe:
      - ``check_same_thread=False``: the pool may hand a connection to whichever
        thread checks it out; paired with per-thread sessions this is safe.
      - WAL journaling + ``busy_timeout``: SQLite permits a single writer at a
        time. WAL lets readers proceed while a writer is active, and
        ``busy_timeout`` makes a contending writer wait for the lock instead of
        failing immediately with "database is locked". ``synchronous=NORMAL`` is
        the standard, durable-enough companion to WAL.

    PostgreSQL specifics:
      - ``pool_size`` / ``max_overflow`` bound the connection pool.
      - ``pool_pre_ping`` validates a pooled connection before checkout so a
        stale/dropped connection is transparently recycled rather than failing
        mid-request.
      - ``pool_recycle_seconds`` recycles connections older than the threshold,
        avoiding server-side idle timeouts.

    The caller owns the returned engine and is responsible for
    ``engine.dispose()`` at shutdown.
    """
    url = db_path if "://" in db_path else f"sqlite:///{db_path}"
    is_sqlite = url.startswith("sqlite")

    if is_sqlite:
        engine = create_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()
    else:
        engine = create_engine(
            url,
            echo=False,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_recycle=pool_recycle_seconds,
            pool_pre_ping=pool_pre_ping,
            # Bound the TCP connect so an unreachable host fails fast (and the
            # startup retry loop stays responsive) instead of hanging on the OS
            # default. psycopg honours ``connect_timeout`` (seconds).
            connect_args={"connect_timeout": 10},
        )

    Base.metadata.create_all(engine)
    Session = scoped_session(sessionmaker(bind=engine))
    return engine, Session
