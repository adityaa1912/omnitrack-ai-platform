"""Recording quota regression tests without application startup or camera access."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base
from backend.recording.manager import RecordingManager
from backend.recording.models import Recording
from backend.recording.storage import LocalFileStorageProvider


@pytest.fixture
def quota_manager(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    storage = LocalFileStorageProvider(str(tmp_path / "recordings"))
    settings = SimpleNamespace(
        recording_retention_hours=24,
        recording_storage_max_bytes=12,
        recording_max_workers=1,
    )
    # Run cleanup explicitly so the background timer cannot race assertions.
    with patch("backend.recording.manager.threading.Thread.start"):
        manager = RecordingManager(db, storage, settings)
    now = datetime.now(timezone.utc)
    for index in range(3):
        storage.write(f"{index}.mp4", b"123456")
        db.add(Recording(
            stream_id="cam1",
            start_time=now - timedelta(minutes=3 - index),
            end_time=now,
            status="completed",
            file_path=f"{index}.mp4",
        ))
    db.commit()
    try:
        yield manager, db, storage
    finally:
        manager.shutdown()
        db.close()
        engine.dispose()


@pytest.mark.parametrize(
    ("limit", "remaining"),
    [(12, ["1.mp4", "2.mp4"]), (6, ["2.mp4"]), (18, ["0.mp4", "1.mp4", "2.mp4"])],
)
def test_quota_deletes_only_until_limit_is_met(quota_manager, limit, remaining):
    manager, db, storage = quota_manager
    manager._settings.recording_storage_max_bytes = limit

    manager._cleanup()

    assert sorted(path.name for path in storage.base_dir.iterdir()) == remaining
    assert [row.file_path for row in db.query(Recording).order_by(Recording.start_time)] == remaining


def test_quota_stops_if_file_deletion_fails(quota_manager):
    manager, db, storage = quota_manager
    with patch.object(storage, "delete", side_effect=OSError("file is in use")):
        manager._cleanup()

    assert sorted(path.name for path in storage.base_dir.iterdir()) == ["0.mp4", "1.mp4", "2.mp4"]
    assert db.query(Recording).count() == 3
