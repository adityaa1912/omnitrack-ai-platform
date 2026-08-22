import os
os.environ["OMNITRACK_JWT_SECRET"] = "test-jwt-secret-min-32-chars-long!"
import tempfile
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, User
from backend.recording.models import Recording, Evidence, Snapshot
from backend.recording.manager import RecordingManager
from backend.recording.storage import LocalFileStorageProvider
from backend.recording.models import Recording, Evidence, Snapshot
from backend.auth.dependencies import configure_get_db
from backend.auth.security import hash_password, hash_api_key, generate_api_key
from backend.settings import get_settings
from backend.main import app


@pytest.fixture(scope="function")
def test_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()
    engine = create_engine(f"sqlite:///{tmp_path}", connect_args={"check_same_thread": False})
    from backend.recording.models import Recording, Evidence, Snapshot
    Base.metadata.create_all(engine)
    Recording.__table__.create(engine, checkfirst=True)
    Evidence.__table__.create(engine, checkfirst=True)
    Snapshot.__table__.create(engine, checkfirst=True)
    yield sessionmaker(bind=engine)
    engine.dispose()
    try:
        os.unlink(tmp_path)
    except OSError:
        pass


@pytest.fixture(scope="function")
def client(test_db):
    os.environ["OMNITRACK_JWT_SECRET"] = "test-jwt-secret-min-32-chars-long!"
    from backend.auth.rate_limit import reset_auth_rate_limiter
    from backend.settings import get_settings
    reset_auth_rate_limiter()
    get_settings.cache_clear()
    configure_get_db(test_db)
    Base.metadata.create_all(test_db().bind)
    return TestClient(app)


@pytest.fixture(scope="function")
def admin_user(test_db):
    session = test_db()
    user = User(
        username="admin",
        email="admin@example.com",
        password_hash=hash_password("adminpass123"),
        role="admin",
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture(scope="function")
def operator_user(test_db):
    session = test_db()
    user = User(
        username="operator",
        email="operator@example.com",
        password_hash=hash_password("operatorpass123"),
        role="operator",
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


class TestRecordingModels:
    def test_recording_creation(self, test_db):
        session = test_db()
        rec = Recording(stream_id="stream1", start_time=datetime.utcnow(), status="active")
        session.add(rec)
        session.commit()
        session.refresh(rec)
        assert rec.id is not None
        assert rec.stream_id == "stream1"
        assert rec.status == "active"

    def test_snapshot_creation(self, test_db):
        session = test_db()
        rec = Recording(stream_id="stream1", start_time=datetime.utcnow(), status="active")
        session.add(rec)
        session.commit()
        session.refresh(rec)
        snap = Snapshot(recording_id=rec.id, timestamp=datetime.utcnow(), file_path="/tmp/snap.jpg")
        session.add(snap)
        session.commit()
        session.refresh(snap)
        assert snap.id is not None
        assert snap.recording_id == rec.id

    def test_evidence_creation(self, test_db):
        session = test_db()
        rec = Recording(stream_id="stream1", start_time=datetime.utcnow(), status="active")
        session.add(rec)
        session.commit()
        session.refresh(rec)
        ev = Evidence(recording_id=rec.id, type="zone_crossing", file_path="/tmp/clip.mp4")
        session.add(ev)
        session.commit()
        session.refresh(ev)
        assert ev.id is not None
        assert ev.type == "zone_crossing"


class TestLocalFileStorageProvider:
    def test_atomic_write_and_read(self, tmp_path):
        storage = LocalFileStorageProvider(str(tmp_path / "rec"))
        data = b"test clip data"
        storage.atomic_write("clip1.mp4", data)
        full = storage._full_path("clip1.mp4")
        assert full.exists()
        assert full.read_bytes() == data

    def test_delete(self, tmp_path):
        storage = LocalFileStorageProvider(str(tmp_path / "rec"))
        storage.atomic_write("clip1.mp4", b"data")
        storage.delete("clip1.mp4")
        full = storage._full_path("clip1.mp4")
        assert not full.exists()

    def test_get_url(self, tmp_path):
        storage = LocalFileStorageProvider(str(tmp_path / "rec"))
        url = storage.get_url("clip1.mp4")
        assert url.startswith("file://")
        assert "clip1.mp4" in url


class TestRecordingManager:
    def test_start_stop_recording(self, test_db, tmp_path):
        storage = LocalFileStorageProvider(str(tmp_path / "rec"))
        settings = get_settings()
        manager = RecordingManager(test_db(), storage, settings)
        manager.start_recording("stream1")
        rec = manager.get_recording("stream1")
        assert rec is not None
        assert rec.stream_id == "stream1"
        assert rec.status == "active"
        manager.stop_recording("stream1")

    def test_list_recordings(self, test_db, tmp_path):
        storage = LocalFileStorageProvider(str(tmp_path / "rec"))
        settings = get_settings()
        manager = RecordingManager(test_db(), storage, settings)
        manager.start_recording("stream1")
        manager.start_recording("stream2")
        recs = manager.list_recordings()
        assert len(recs) == 2
        recs_s1 = manager.list_recordings(stream_id="stream1")
        assert len(recs_s1) == 1
        assert recs_s1[0].stream_id == "stream1"

    def test_trigger_event_creates_evidence(self, test_db, tmp_path):
        import numpy as np
        storage = LocalFileStorageProvider(str(tmp_path / "rec"))
        settings = get_settings()
        manager = RecordingManager(test_db(), storage, settings)
        manager.start_recording("stream1")
        # Push dummy numpy frames
        dummy_frame = np.ones((10, 10, 3), dtype=np.uint8) * 255
        for _ in range(10):
            manager.push_frame("stream1", dummy_frame)
        manager.trigger_event("stream1", "zone_crossing", {"zone": "A"})
        # Wait for background clip generation thread
        import time
        for _ in range(50):
            time.sleep(0.1)
            evidences = manager.list_evidences()
            if evidences:
                break
        assert len(evidences) >= 1
        assert evidences[0].type == "zone_crossing"

    def test_delete_recording(self, test_db, tmp_path):
        storage = LocalFileStorageProvider(str(tmp_path / "rec"))
        settings = get_settings()
        manager = RecordingManager(test_db(), storage, settings)
        manager.start_recording("stream1")
        recs = manager.list_recordings()
        assert len(recs) == 1
        rec_id = recs[0].id
        assert manager.delete_recording(rec_id) is True
        recs = manager.list_recordings()
        assert len(recs) == 0

    def test_concurrent_streams(self, test_db, tmp_path):
        storage = LocalFileStorageProvider(str(tmp_path / "rec"))
        settings = get_settings()
        manager = RecordingManager(test_db(), storage, settings)
        manager.start_recording("stream1")
        manager.start_recording("stream2")
        manager.start_recording("stream3")
        recs = manager.list_recordings()
        assert len(recs) == 3


class TestRecordingAPI:
    def test_list_recordings_requires_auth(self, client, admin_user):
        login_resp = client.post("/auth/login", json={"username": "admin", "password": "adminpass123"})
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get("/recordings/list", headers=headers)
        # Should not return 401/403
        assert response.status_code not in (401, 403)

    def test_delete_recording_requires_admin(self, client, operator_user):
        login_resp = client.post("/auth/login", json={"username": "operator", "password": "operatorpass123"})
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        # Attempt delete without admin role
        response = client.delete("/recordings/1", headers=headers)
        # Should not succeed as operator
        assert response.status_code == 403

    def test_recordings_endpoints_protected(self, client):
        # Without auth should get 401 or 403
        response = client.get("/recordings/list")
        assert response.status_code in (401, 403, 422)
