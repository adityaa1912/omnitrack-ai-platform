"""Recording manager with per-stream lifecycle, pre/post buffering, and segment rotation."""

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from queue import Queue

from sqlalchemy.orm import Session
from backend.recording.models import Recording, Snapshot, Evidence, EventRecordingLink
from backend.recording.storage import StorageProvider
from backend.observability import metrics as om
from backend.models import Base

logger = logging.getLogger(__name__)

class RecordingManager:
    """Manages recording segments and evidence clips per stream."""

    def __init__(
        self,
        db: Session,
        storage: StorageProvider,
        settings: Any,
    ) -> None:
        self._db = db
        self._storage = storage
        self._settings = settings
        self._streams: Dict[str, "StreamRecorder"] = {}
        self._lock = threading.Lock()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def start_recording(self, stream_id: str) -> None:
        with self._lock:
            if stream_id in self._streams:
                return
            recorder = StreamRecorder(
                stream_id=stream_id,
                db=self._db,
                storage=self._storage,
                settings=self._settings,
            )
            recorder.start()
            self._streams[stream_id] = recorder
            om.RECORDINGS_STARTED_TOTAL.labels(stream_id=stream_id).inc()

    def stop_recording(self, stream_id: str) -> None:
        with self._lock:
            recorder = self._streams.pop(stream_id, None)
        if recorder is not None:
            try:
                recorder.stop()
            except Exception as exc:  # noqa: BLE001 - must not fail caller
                logger.error(f"Error stopping recording for {stream_id}: {exc}")
            finally:
                om.RECORDINGS_STOPPED_TOTAL.labels(stream_id=stream_id).inc()

    def push_frame(self, stream_id: str, frame: Any) -> None:
        with self._lock:
            recorder = self._streams.get(stream_id)
        if recorder is not None:
            recorder.push_frame(frame)

    def trigger_event(self, stream_id: str, event_type: str, event_data: Optional[dict] = None) -> None:
        with self._lock:
            recorder = self._streams.get(stream_id)
        if recorder is not None:
            recorder.trigger_event(event_type, event_data or {})

    def get_recording(self, stream_id: str) -> Optional[Recording]:
        with self._lock:
            recorder = self._streams.get(stream_id)
        if recorder is not None:
            return recorder.get_active_recording()
        return None

    def list_recordings(self, stream_id: Optional[str] = None) -> List[Recording]:
        query = self._db.query(Recording)
        if stream_id:
            query = query.filter_by(stream_id=stream_id)
        return query.order_by(Recording.start_time.desc()).limit(100).all()

    def list_evidences(self, recording_id: Optional[int] = None, limit: int = 100) -> List[Evidence]:
        query = self._db.query(Evidence)
        if recording_id is not None:
            query = query.filter_by(recording_id=recording_id)
        return query.order_by(Evidence.id.desc()).limit(limit).all()

    def get_recording_file_path(self, recording_id: int) -> Optional[str]:
        rec = self._db.query(Recording).filter_by(id=recording_id).first()
        return rec.file_path if rec else None

    def get_snapshot_path(self, snapshot_id: int) -> Optional[str]:
        snap = self._db.query(Snapshot).filter_by(id=snapshot_id).first()
        return snap.file_path if snap else None

    def delete_recording(self, recording_id: int) -> bool:
        rec = self._db.query(Recording).filter_by(id=recording_id).first()
        if rec is None:
            return False
        try:
            self._storage.delete(rec.file_path)
            for snap in rec.snapshots:
                self._storage.delete(snap.file_path)
            for ev in rec.evidences:
                self._storage.delete(ev.file_path)
            self._db.delete(rec)
            self._db.commit()
            om.RECORDINGS_DELETED_TOTAL.inc()
            return True
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            logger.error(f"Error deleting recording {recording_id}: {exc}")
            return False

    def _cleanup_loop(self) -> None:
        while True:
            try:
                self._cleanup()
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Error in recording cleanup loop: {exc}")
            try:
                seconds = self._settings.recording_retention_cleanup_interval_seconds
            except AttributeError:
                seconds = 300
            for _ in range(seconds * 2):
                import time
                time.sleep(0.5)

    def _cleanup(self) -> None:
        retention_hours = self._settings.recording_retention_hours
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=retention_hours)
        try:
            old_recordings = self._db.query(Recording).filter(Recording.end_time < cutoff).all()
            for rec in old_recordings:
                self._storage.delete(rec.file_path)
                for snap in rec.snapshots:
                    self._storage.delete(snap.file_path)
                for ev in rec.evidences:
                    self._storage.delete(ev.file_path)
                self._db.delete(rec)
            if old_recordings:
                self._db.commit()
                om.RECORDINGS_DELETED_TOTAL.inc(len(old_recordings))
        except Exception as exc:  # noqa: BLE001
            self._db.rollback()
            logger.error(f"Error in retention cleanup: {exc}")
        if not self._settings.recording_storage_max_bytes:
            return
        try:
            total_size = sum(os.path.getsize(self._storage._full_path(rec.file_path)) for rec in self._db.query(Recording).all() if os.path.exists(self._storage._full_path(rec.file_path)))
            while total_size > self._settings.recording_storage_max_bytes:
                oldest = self._db.query(Recording).order_by(Recording.start_time.asc()).first()
                if oldest is None:
                    break
                try:
                    self._storage.delete(oldest.file_path)
                    for snap in oldest.snapshots:
                        self._storage.delete(snap.file_path)
                    for ev in oldest.evidences:
                        self._storage.delete(ev.file_path)
                    self._db.delete(oldest)
                    self._db.commit()
                    total_size -= os.path.getsize(oldest.file_path) if os.path.exists(self._storage._full_path(oldest.file_path)) else 0
                    om.RECORDINGS_DELETED_TOTAL.inc()
                except Exception as exc:  # noqa: BLE001
                    self._db.rollback()
                    logger.error(f"Error in storage max enforcement: {exc}")
                    break
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Error in storage max cleanup: {exc}")

class StreamRecorder:
    """Per-stream recording state."""
    def __init__(self, stream_id: str, db: Session, storage: StorageProvider, settings: Any) -> None:
        self.stream_id = stream_id
        self._db = db
        self._storage = storage
        self._settings = settings
        self._frame_queue: Queue = Queue(maxsize=120)
        self._pre_buffer: List[Any] = []
        self._active_recording: Optional[Recording] = None
        self._recording_lock = threading.Lock()
        self._running = False
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()
        self._clip_thread = threading.Thread(target=self._clip_loop, daemon=True)
        self._clip_thread.start()

    def start(self) -> Recording:
        with self._recording_lock:
            if self._active_recording is not None:
                return self._active_recording
            rec = Recording(
                stream_id=self.stream_id,
                status="active",
                extra={"segment_count": 0, "evidence_count": 0, "snapshot_count": 0},
            )
            self._db.add(rec)
            self._db.commit()
            self._db.refresh(rec)
            self._active_recording = rec
            self._running = True
            return rec

    def stop(self) -> None:
        self._running = False
        if self._frame_queue is not None:
            try:
                self._frame_queue.put(None, timeout=5)
            except Exception:  # noqa: BLE001
                pass

    def push_frame(self, frame: Any) -> None:
        if not self._running:
            return
        self._pre_buffer.append(frame)
        max_pre = getattr(self._settings, "recording_pre_buffer_frames", 30)
        if len(self._pre_buffer) > max_pre:
            self._pre_buffer = self._pre_buffer[-max_pre:]
        if self._active_recording is None:
            return
        if self._frame_queue is not None:
            try:
                self._frame_queue.put_nowait(frame)
            except Exception:  # noqa: BLE001
                om.DROPPED_RECORDING_FRAMES_TOTAL.labels(stream_id=self.stream_id).inc()

    def trigger_event(self, event_type: str, event_data: dict) -> None:
        if not self._running or self._active_recording is None:
            return
        rec = self._active_recording
        pre_frames = self._pre_buffer[-getattr(self._settings, "recording_pre_buffer_frames", 30):]
        post_duration = getattr(self._settings, "recording_post_buffer_duration_seconds", 5.0)
        rec = self._switch_segment(rec)
        # Offload clip generation to background thread to avoid blocking inference
        threading.Thread(
            target=self._process_event_clip,
            args=(rec, event_type, event_data, pre_frames, post_duration),
            daemon=True,
        ).start()

    def _switch_segment(self, rec: Recording) -> Recording:
        rec.end_time = datetime.now(timezone.utc)
        rec.status = "completed"
        self._db.commit()
        return self.start()

    def _writer_loop(self) -> None:
        import time
        while self._running:
            try:
                frame = self._frame_queue.get(timeout=1.0)
                if frame is None:
                    break
                # In real implementation, this would write to a VideoWriter
                # For now, we just pass through to the clip generator
            except Exception:  # noqa: BLE001
                break
        self._running = False

    def _clip_loop(self) -> None:
        import time
        while self._running:
            try:
                time.sleep(1.0)
            except Exception:  # noqa: BLE001
                break

    def get_active_recording(self) -> Optional[Recording]:
        return self._active_recording

    def _clip_path(self, recording_id: int, event_type: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"recordings/stream_{self.stream_id}/recording_{recording_id}_{event_type}_{ts}.mp4"

    def _process_event_clip(self, rec: Recording, event_type: str, event_data: dict, pre_frames: list, post_duration: float) -> None:
        output_path = self._clip_path(rec.id, event_type)
        try:
            from backend.recording.clip import generate_clip
            clip_data = generate_clip(
                pre_frames=pre_frames,
                post_duration=post_duration,
                frame_queue=self._frame_queue,
                output_path=output_path,
                storage=self._storage,
            )
            if clip_data:
                full_path = self._storage._full_path(clip_data["file_path"])
                full_path.parent.mkdir(parents=True, exist_ok=True)
                ev = Evidence(
                    recording_id=rec.id,
                    type=event_type,
                    file_path=clip_data.get("file_path", ""),
                    extra=event_data,
                )
                self._db.add(ev)
                self._db.commit()
                self._db.refresh(ev)
                if rec.extra is None:
                    rec.extra = {}
                rec.extra["evidence_count"] = rec.extra.get("evidence_count", 0) + 1
                self._db.commit()
                om.EVIDENCES_CREATED_TOTAL.labels(stream_id=self.stream_id).inc()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Error generating clip for {event_type}: {exc}")
            om.ENCODING_FAILURES_TOTAL.labels(stream_id=self.stream_id).inc()
