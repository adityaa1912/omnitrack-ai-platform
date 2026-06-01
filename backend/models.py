"""
Database models for persistence of inference data.

Stores detections, tracking data, and metrics for historical analysis.
Uses SQLAlchemy ORM for clean database abstraction.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, Float, String, DateTime, Boolean, JSON, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

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


def get_database_session(db_path: str = "inference_data.db"):
    """Create database session factory."""
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()
