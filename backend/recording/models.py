"""Recording and evidence ORM models."""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Float, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship
from backend.models import Base

class Recording(Base):
    __tablename__ = "recordings"
    id = Column(Integer, primary_key=True)
    stream_id = Column(String, index=True, nullable=False)
    start_time = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    end_time = Column(DateTime, nullable=True, index=True)
    file_path = Column(String, nullable=True, default="")
    size_bytes = Column(Integer, nullable=False, default=0)
    status = Column(String, default="pending", index=True)
    extra = Column(JSON, nullable=True)
    snapshots = relationship("Snapshot", back_populates="recording", cascade="all, delete-orphan")
    evidences = relationship("Evidence", back_populates="recording", cascade="all, delete-orphan")

class Snapshot(Base):
    __tablename__ = "snapshots"
    id = Column(Integer, primary_key=True)
    recording_id = Column(Integer, ForeignKey("recordings.id"), nullable=False, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    file_path = Column(String, nullable=False)
    extra = Column(JSON, nullable=True)
    recording = relationship("Recording", back_populates="snapshots")

class Evidence(Base):
    __tablename__ = "evidences"
    id = Column(Integer, primary_key=True)
    recording_id = Column(Integer, ForeignKey("recordings.id"), nullable=False, index=True)
    type = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    extra = Column(JSON, nullable=True)
    recording = relationship("Recording", back_populates="evidences")

class EventRecordingLink(Base):
    __tablename__ = "event_recording_links"
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, nullable=False, index=True)
    recording_id = Column(Integer, ForeignKey("recordings.id"), nullable=False, index=True)
    recording = relationship("Recording", back_populates="event_links")

Recording.event_links = relationship("EventRecordingLink", back_populates="recording", cascade="all, delete-orphan")
