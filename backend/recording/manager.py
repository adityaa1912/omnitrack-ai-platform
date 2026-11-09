"""Recording manager with per-stream lifecycle, pre/post buffering, and segment rotation."""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session, sessionmaker
from backend.recording.models import Recording, Snapshot, Evidence, EventRecordingLink
from backend.recording.storage import StorageProvider
from backend.observability import metrics as om
from backend.models import Base

logger = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np
except Exception:  # noqa: BLE001
    cv2 = None
    np = None


class RecordingManager:
    """Manages recording segments and evidence clips per stream."""

    def __init__(
        self,
        db: Session,
        storage: StorageProvider,
        settings: Any,
    ) -> None:
        self._session_factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
        self._storage = storage
        self._settings = settings
        self._streams: Dict[str, "StreamRecorder"] = {}
        self._lock = threading.Lock()

        # Bounded shared worker pool for I/O operations (writing frames, encoding clips, taking snapshots)
        max_workers = getattr(self._settings, "recording_max_workers", 4)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="rec_worker")

        self._is_shutdown = False
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def _stop_timeout(self) -> float:
        return float(getattr(self._settings, "stop_timeout_seconds", None) or getattr(self._settings, "stream_stop_timeout_seconds", 10.0) or 10.0)

    def _recording_fps(self) -> float:
        return float(getattr(self._settings, "recording_fps", None) or 10.0)

        self._is_shutdown = False

        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def shutdown(self) -> None:
        """Gracefully shut down the recording manager and its background workers.

        Stops every per-stream recorder (flushing its writer and finalizing the
        recording row) before draining the shared executor, so no clip is left
        half-encoded and no in-use resource is torn down while a worker is alive.
        """
        self._is_shutdown = True
        with self._lock:
            stream_ids = list(self._streams.keys())
        for stream_id in stream_ids:
            try:
                self.stop_recording(stream_id)
            except Exception as exc:  # noqa: BLE001 - shutdown must be resilient
                logger.error(f"Error stopping recording for {stream_id} during shutdown: {exc}")
        self._executor.shutdown(wait=True)
        if self._cleanup_thread is not None and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5.0)

    def start_recording(self, stream_id: str) -> None:
        with self._lock:
            if stream_id in self._streams:
                return
            recorder = StreamRecorder(
                stream_id=stream_id,
                session_factory=self._session_factory,
                storage=self._storage,
                settings=self._settings,
                executor=self._executor,
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
        session = self._session_factory()
        try:
            query = session.query(Recording)
            if stream_id:
                query = query.filter_by(stream_id=stream_id)
            return query.order_by(Recording.start_time.desc()).limit(100).all()
        finally:
            session.close()

    def list_evidences(self, recording_id: Optional[int] = None, limit: int = 100) -> List[Evidence]:
        session = self._session_factory()
        try:
            query = session.query(Evidence)
            if recording_id is not None:
                query = query.filter_by(recording_id=recording_id)
            return query.order_by(Evidence.id.desc()).limit(limit).all()
        finally:
            session.close()

    def get_recording_file_path(self, recording_id: int) -> Optional[str]:
        session = self._session_factory()
        try:
            rec = session.query(Recording).filter_by(id=recording_id).first()
            return rec.file_path if rec else None
        finally:
            session.close()

    def get_snapshot_path(self, snapshot_id: int) -> Optional[str]:
        session = self._session_factory()
        try:
            snap = session.query(Snapshot).filter_by(id=snapshot_id).first()
            return snap.file_path if snap else None
        finally:
            session.close()

    def delete_recording(self, recording_id: int) -> bool:
        session = self._session_factory()
        try:
            rec = session.query(Recording).filter_by(id=recording_id).first()
            if rec is None:
                return False
            try:
                self._storage.delete(rec.file_path)
                for snap in rec.snapshots:
                    self._storage.delete(snap.file_path)
                for ev in rec.evidences:
                    self._storage.delete(ev.file_path)
                session.delete(rec)
                session.commit()
                om.RECORDINGS_DELETED_TOTAL.inc()
                return True
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                logger.error(f"Error deleting recording {recording_id}: {exc}")
                return False
        finally:
            session.close()

    def _cleanup_loop(self) -> None:
        while not self._is_shutdown:
            try:
                self._cleanup()
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Error in recording cleanup loop: {exc}")
            try:
                seconds = self._settings.recording_retention_cleanup_interval_seconds
            except AttributeError:
                seconds = 300
            for _ in range(seconds * 2):
                if self._is_shutdown:
                    break
                time.sleep(0.5)

    def _cleanup(self) -> None:
        session = self._session_factory()
        try:
            retention_hours = self._settings.recording_retention_hours
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=retention_hours)
            try:
                old_recordings = session.query(Recording).filter(Recording.end_time < cutoff).all()
                for rec in old_recordings:
                    self._storage.delete(rec.file_path)
                    for snap in rec.snapshots:
                        self._storage.delete(snap.file_path)
                    for ev in rec.evidences:
                        self._storage.delete(ev.file_path)
                    session.delete(rec)
                if old_recordings:
                    session.commit()
                    om.RECORDINGS_DELETED_TOTAL.inc(len(old_recordings))
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                logger.error(f"Error in retention cleanup: {exc}")
            if not self._settings.recording_storage_max_bytes:
                return
            try:
                total_size = sum(
                    os.path.getsize(self._storage._full_path(rec.file_path))
                    for rec in session.query(Recording).all()
                    if os.path.exists(self._storage._full_path(rec.file_path))
                )
                while total_size > self._settings.recording_storage_max_bytes:
                    oldest = session.query(Recording).order_by(Recording.start_time.asc()).first()
                    if oldest is None:
                        break
                    try:
                        full_path = self._storage._full_path(oldest.file_path)
                        size_bytes = os.path.getsize(full_path) if os.path.exists(full_path) else 0
                        self._storage.delete(oldest.file_path)
                        for snap in oldest.snapshots:
                            self._storage.delete(snap.file_path)
                        for ev in oldest.evidences:
                            self._storage.delete(ev.file_path)
                        session.delete(oldest)
                        session.commit()
                        total_size -= size_bytes
                        om.RECORDINGS_DELETED_TOTAL.inc()
                    except Exception as exc:  # noqa: BLE001
                        session.rollback()
                        logger.error(f"Error in storage max enforcement: {exc}")
                        break
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Error in storage max cleanup: {exc}")
        finally:
            session.close()


class StreamRecorder:
    """Per-stream recording state.

    Writes pushed frames to a bounded continuous segment file via cv2, and
    produces independent event clips on demand (pre + post buffer) without
    touching the continuous writer's output.
    """

    def __init__(
        self,
        stream_id: str,
        session_factory: sessionmaker,
        storage: StorageProvider,
        settings: Any,
        executor: ThreadPoolExecutor,
    ) -> None:
        self.stream_id = stream_id
        self._session_factory = session_factory
        self._storage = storage
        self._settings = settings
        self._executor = executor
        self._frame_queue: Queue = Queue(maxsize=120)
        self._pre_buffer: List[Any] = []
        self._active_recording: Optional[Recording] = None
        self._recording_lock = threading.Lock()
        self._running = False
        self._writer_thread: Optional[threading.Thread] = None
        self._writer_stop = threading.Event()
        self._writer_path: Optional[str] = None
        self._writer: Any = None
        self._writer_frame_size: Optional[tuple] = None
        self._writer_fps: float = 0.0
        self._writer_frame_count = 0

    def start(self) -> Recording:
        with self._recording_lock:
            if self._active_recording is not None:
                return self._active_recording
            session = self._session_factory()
            try:
                rec = Recording(
                    stream_id=self.stream_id,
                    status="active",
                    extra={"segment_count": 0, "evidence_count": 0, "snapshot_count": 0},
                )
                session.add(rec)
                session.commit()
                session.refresh(rec)
                self._active_recording = rec
                self._running = True
                self._writer_stop.clear()
                self._writer_thread = threading.Thread(
                    target=self._writer_loop, daemon=True, name=f"rec-writer-{self.stream_id}"
                )
                self._writer_thread.start()
                return rec
            finally:
                session.close()

    def stop(self) -> None:
        with self._recording_lock:
            if not self._running:
                return
            self._running = False
            self._writer_stop.set()
            try:
                self._frame_queue.put(None, timeout=5)
            except Exception:  # noqa: BLE001
                pass
            rec = self._active_recording
            self._active_recording = None

        # Finalize the writer on its own thread before touching the DB row, so
        # we never release the recording while video is still being written.
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=self._stop_timeout())
        self._finalize_writer()
        self._writer_thread = None

        if rec is not None:
            session = self._session_factory()
            try:
                db_rec = session.get(Recording, rec.id)
                if db_rec is not None:
                    db_rec.end_time = datetime.now(timezone.utc)
                    db_rec.status = "completed"
                    if self._writer_path and os.path.exists(self._storage._full_path(self._writer_path)):
                        try:
                            db_rec.size_bytes = os.path.getsize(self._storage._full_path(self._writer_path))
                        except OSError:
                            db_rec.size_bytes = 0
                        db_rec.file_path = self._writer_path
                    if db_rec.extra is None:
                        db_rec.extra = {}
                    db_rec.extra["segment_count"] = db_rec.extra.get("segment_count", 0) + 1
                    session.commit()
            finally:
                session.close()

    def push_frame(self, frame: Any) -> None:
        if not self._running:
            return
        self._pre_buffer.append(frame)
        max_pre = getattr(self._settings, "recording_pre_buffer_frames", 30)
        if len(self._pre_buffer) > max_pre:
            self._pre_buffer = self._pre_buffer[-max_pre:]
        if self._active_recording is None:
            return
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
        # Offload clip generation to the shared background executor to avoid blocking inference
        self._executor.submit(
            self._process_event_clip,
            rec, event_type, event_data, pre_frames, post_duration
        )

    def _switch_segment(self, rec: Recording) -> Recording:
        session = self._session_factory()
        try:
            db_rec = session.get(Recording, rec.id)
            if db_rec is None:
                return rec
            db_rec.end_time = datetime.now(timezone.utc)
            db_rec.status = "completed"
            if db_rec.extra is None:
                db_rec.extra = {}
            db_rec.extra["segment_count"] = db_rec.extra.get("segment_count", 0) + 1
            session.commit()
            session.refresh(db_rec)
            # Start a fresh active segment
            new_rec = Recording(
                stream_id=self.stream_id,
                status="active",
                extra={"segment_count": 0, "evidence_count": 0, "snapshot_count": 0},
            )
            session.add(new_rec)
            session.commit()
            session.refresh(new_rec)
            with self._recording_lock:
                self._active_recording = new_rec
            return new_rec
        finally:
            session.close()

    def _writer_loop(self) -> None:
        """Continuously drains the frame queue and writes encoded video to a segment file."""
        fourcc = None
        if cv2 is not None:
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        segment_idx = 0
        try:
            while not self._writer_stop.is_set():
                try:
                    frame = self._frame_queue.get(timeout=0.25)
                except Empty:
                    continue
                if frame is None:
                    break
                self._ensure_writer_open(frame, segment_idx)
                if self._writer is not None and cv2 is not None and np is not None:
                    try:
                        image = frame.get("image") if isinstance(frame, dict) else frame
                        if isinstance(image, np.ndarray):
                            if image.ndim == 2:
                                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
                            if self._writer_frame_size is not None and (image.shape[1], image.shape[0]) != self._writer_frame_size:
                                image = cv2.resize(image, self._writer_frame_size)
                            self._writer.write(image)
                            self._writer_frame_count += 1
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            self._finalize_writer_locked()

    def _ensure_writer_open(self, frame: Any, segment_idx: int) -> None:
        if self._writer is not None:
            return
        if cv2 is None or np is None:
            return
        image = frame.get("image") if isinstance(frame, dict) else frame
        if not isinstance(image, np.ndarray):
            return
        h, w = image.shape[:2]
        self._writer_frame_size = (w, h)
        self._writer_fps = float(getattr(self._settings, "recording_fps", 10.0) or 10.0)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = f"recordings/stream_{self.stream_id}/segment_{segment_idx}_{ts}.mp4"
        self._writer_path = path
        full_path = self._storage._full_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        self._writer = cv2.VideoWriter(str(full_path), cv2.VideoWriter_fourcc(*"mp4v"), self._writer_fps, (w, h))

    def _finalize_writer_locked(self) -> None:
        if self._writer is not None:
            try:
                self._writer.release()
            except Exception:  # noqa: BLE001
                pass
            self._writer = None

    def _finalize_writer(self) -> None:
        self._finalize_writer_locked()

    def get_active_recording(self) -> Optional[Recording]:
        return self._active_recording

    def _clip_path(self, recording_id: int, event_type: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return f"recordings/stream_{self.stream_id}/recording_{recording_id}_{event_type}_{ts}.mp4"

    def _process_event_clip(self, rec: Recording, event_type: str, event_data: dict, pre_frames: list, post_duration: float) -> None:
        session = self._session_factory()
        try:
            output_path = self._clip_path(rec.id, event_type)
            from backend.recording.clip import generate_clip
            clip_data = generate_clip(
                pre_frames=pre_frames,
                post_duration=post_duration,
                frame_queue=self._frame_queue,
                output_path=output_path,
                storage=self._storage,
            )
            if clip_data and clip_data.get("ok"):
                full_path = self._storage._full_path(clip_data["file_path"])
                full_path.parent.mkdir(parents=True, exist_ok=True)
                ev = Evidence(
                    recording_id=rec.id,
                    type=event_type,
                    file_path=clip_data.get("file_path", ""),
                    extra=event_data,
                )
                session.add(ev)
                session.commit()
                session.refresh(ev)
                db_rec = session.get(Recording, rec.id)
                if db_rec is not None:
                    if db_rec.extra is None:
                        db_rec.extra = {}
                    db_rec.extra["evidence_count"] = db_rec.extra.get("evidence_count", 0) + 1
                    session.commit()
                om.EVIDENCES_CREATED_TOTAL.labels(stream_id=self.stream_id).inc()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Error generating clip for {event_type}: {exc}")
            om.ENCODING_FAILURES_TOTAL.labels(stream_id=self.stream_id).inc()
        finally:
            session.close()
