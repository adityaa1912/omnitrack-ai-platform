from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from typing import List, Optional

from backend.auth.dependencies import get_db, get_current_user, CurrentUser, require_role
from backend.recording.manager import RecordingManager
from backend.recording.models import Recording, Evidence
from backend.observability import metrics as om

router = APIRouter(prefix="/recordings", tags=["recordings"])

def get_manager():
    # Global manager registered at startup in main.py
    return _manager


def set_manager(manager: RecordingManager) -> None:
    global _manager
    _manager = manager


_manager: RecordingManager = None


@router.get("")
def list_recordings(
    stream_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    current_user: CurrentUser = Depends(get_current_user),
):
    manager = get_manager()
    if manager is None:
        raise HTTPException(status_code=503, detail="Recording system not initialized")
    try:
        records = manager.list_recordings(stream_id=stream_id)
        result = []
        for r in records[:limit]:
            if isinstance(r, Recording):
                result.append({
                    "id": r.id,
                    "stream_id": r.stream_id,
                    "start_time": r.start_time,
                    "end_time": r.end_time,
                    "status": r.status,
                    "size_bytes": r.size_bytes,
                    "metadata": r.metadata,
                })
        om.RECORDINGS_LISTED_TOTAL.inc(len(result))
        return result
    except Exception as exc:
        om.API_REQUEST_ERRORS_TOTAL.inc()
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{recording_id}")
def get_recording(
    recording_id: int,
    current_user: CurrentUser = Depends(get_current_user),
):
    from sqlalchemy.orm import Session
    from backend.auth.dependencies import get_db
    db = next(get_db())
    try:
        rec = db.query(Recording).filter_by(id=recording_id).first()
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        return {
            "id": rec.id,
            "stream_id": rec.stream_id,
            "start_time": rec.start_time,
            "end_time": rec.end_time,
            "status": rec.status,
            "size_bytes": rec.size_bytes,
            "metadata": rec.metadata,
        }
    finally:
        db.close()


@router.get("/{recording_id}/file")
def get_recording_file(
    recording_id: int,
    current_user: CurrentUser = Depends(get_current_user),
):
    manager = get_manager()
    if manager is None:
        raise HTTPException(status_code=503, detail="Recording system not initialized")
    file_path = manager.get_recording_file_path(recording_id)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Recording file not found")
    return FileResponse(file_path, media_type="video/mp4")


@router.get("/{recording_id}/evidences")
def get_recording_evidences(
    recording_id: int,
    limit: int = Query(100, ge=1, le=1000),
    current_user: CurrentUser = Depends(get_current_user),
):
    from sqlalchemy.orm import Session
    from backend.auth.dependencies import get_db
    db = next(get_db())
    try:
        evidences = db.query(Evidence).filter_by(recording_id=recording_id).order_by(Evidence.id.desc()).limit(limit).all()
        return [
            {
                "id": e.id,
                "type": e.type,
                "file_path": e.file_path,
                "metadata": e.extra,
                "created_at": e.created_at,
            }
            for e in evidences
        ]
    finally:
        db.close()


@router.delete("/{recording_id}")
def delete_recording(
    recording_id: int,
    current_user: CurrentUser = Depends(require_role("admin")),
):
    manager = get_manager()
    if manager is None:
        raise HTTPException(status_code=503, detail="Recording system not initialized")
    success = manager.delete_recording(recording_id)
    if not success:
        raise HTTPException(status_code=404, detail="Recording not found")
    return {"status": "deleted"}


@router.get("/evidences")
def list_evidences(
    recording_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    current_user: CurrentUser = Depends(get_current_user),
):
    manager = get_manager()
    if manager is None:
        raise HTTPException(status_code=503, detail="Recording system not initialized")
    evidences = manager.list_evidences(recording_id=recording_id, limit=limit)
    result = []
    for e in evidences:
        if isinstance(e, Evidence):
            result.append({
                "id": e.id,
                "recording_id": e.recording_id,
                "type": e.type,
                "file_path": e.file_path,
                "metadata": e.extra,
            })
    return result
