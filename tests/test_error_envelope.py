import os
os.environ.setdefault("OMNITRACK_JWT_SECRET", "test-jwt-secret-min-32-chars-long!")
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, User
from backend.auth.dependencies import configure_get_db
from backend.auth.security import hash_password
from backend.settings import get_settings
from backend.main import app


SECRET = "db password=hunter2 host=internal.db.example.com"


@pytest.fixture(scope="function")
def test_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()
    engine = create_engine(
        f"sqlite:///{tmp_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        User(
            username="admin",
            email="admin@example.com",
            password_hash=hash_password("adminpass123"),
            role="admin",
            is_active=True,
        )
    )
    session.commit()
    session.close()
    yield sessionmaker(bind=engine)
    engine.dispose()
    try:
        os.unlink(tmp_path)
    except OSError:
        pass


@pytest.fixture(scope="function")
def client(test_db):
    from backend.auth.rate_limit import reset_auth_rate_limiter

    reset_auth_rate_limiter()
    get_settings.cache_clear()
    configure_get_db(test_db)
    c = TestClient(app, raise_server_exceptions=False)
    login = c.post(
        "/auth/login", json={"username": "admin", "password": "adminpass123"}
    )
    token = login.json()["access_token"]
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


def test_unhandled_exception_returns_generic_envelope(client, monkeypatch):
    def explode(stream_id):
        raise RuntimeError(f"secret detail: {SECRET}")

    monkeypatch.setattr("backend.main.service.get_stream_regions", explode)
    response = client.get("/stream/s1/regions")
    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Internal server error"
    assert SECRET not in response.text
    assert "RuntimeError" not in response.text


def test_stop_stream_httpexception_detail_sanitized(client, monkeypatch):
    def explode(stream_id):
        raise RuntimeError(f"secret detail: {SECRET}")

    monkeypatch.setattr("backend.main.service.stop_stream", explode)
    response = client.post("/stream/stop", params={"stream_id": "s1"})
    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to stop stream"
    assert SECRET not in response.text


def test_404_preserves_detail(client, monkeypatch):
    def not_found(stream_id):
        raise ValueError(f"Stream {stream_id} not found")

    monkeypatch.setattr("backend.main.service.stop_stream", not_found)
    response = client.post("/stream/stop", params={"stream_id": "gone"})
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_validation_error_preserves_status_and_detail(client):
    response = client.post("/stream/stop")
    assert response.status_code == 422
    body = response.json()
    assert "detail" in body


def test_unknown_route_returns_404_envelope(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_success_response_shape_unchanged(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert "active_streams" in body
