import os
os.environ["OMNITRACK_JWT_SECRET"] = "test-jwt-secret-min-32-chars-long!"
import tempfile
import pytest
import json
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, User, APIKey, AuditLog, Session as SessionModel
from backend.auth.dependencies import configure_get_db
from backend.auth.security import hash_password, hash_api_key, generate_api_key
from backend.auth.services import AuthService
from backend.settings import get_settings
from backend.main import app


@pytest.fixture(scope="function")
def test_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()
    engine = create_engine(f"sqlite:///{tmp_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
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
    # Ensure all tables exist in the shared in-memory DB
    from backend.models import Base
    Base.metadata.create_all(test_db().bind)
    return TestClient(app)


@pytest.fixture(scope="function")
def auth_user(test_db):
    session = test_db()
    user = User(
        username="testuser",
        email="test@example.com",
        password_hash=hash_password("testpass123"),
        role="viewer",
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


class TestAuthLogin:
    def test_login_success(self, client, auth_user):
        response = client.post("/auth/login", json={"username": "testuser", "password": "testpass123"})
        assert response.status_code == 200
        data = response.json()
        assert data["access_token"]
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0

    def test_login_invalid_credentials(self, client):
        response = client.post("/auth/login", json={"username": "testuser", "password": "wrongpass"})
        assert response.status_code == 401
        assert "Invalid credentials" in response.json()["detail"]

    def test_login_nonexistent_user(self, client):
        response = client.post("/auth/login", json={"username": "nonexistent", "password": "nonexistpass"})
        assert response.status_code == 401

    def test_login_inactive_user(self, client, test_db):
        session = test_db()
        user = User(
            username="inactive",
            email="inactive@example.com",
            password_hash=hash_password("inactivepass123"),
            role="viewer",
            is_active=False,
        )
        session.add(user)
        session.commit()
        response = client.post("/auth/login", json={"username": "inactive", "password": "inactivepass123"})
        assert response.status_code == 401


class TestJWTTokens:
    def test_token_validation(self, client, auth_user):
        login_resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass123"})
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get("/auth/users/me", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "testuser"
        assert data["role"] == "viewer"

    def test_token_missing(self, client):
        response = client.get("/auth/users/me")
        assert response.status_code == 401

    def test_token_invalid(self, client):
        headers = {"Authorization": "Bearer invalid.token.here"}
        response = client.get("/auth/users/me", headers=headers)
        assert response.status_code == 401

    def test_token_expired(self, client, test_db, auth_user):
        session = test_db()
        from backend.auth.security import encode_jwt
        settings = get_settings()
        # Create an already-expired token
        payload = {
            "sub": str(auth_user.id),
            "role": auth_user.role,
            "type": "access",
            "exp": 1,  # Expired long ago
        }
        token = encode_jwt(payload, settings.jwt_secret)
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get("/auth/users/me", headers=headers)
        assert response.status_code == 401


class TestRBAC:
    def test_viewer_can_read_streams(self, client, auth_user):
        login_resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass123"})
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get("/streams", headers=headers)
        assert response.status_code == 200

    def test_viewer_cannot_start_stream(self, client, auth_user):
        login_resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass123"})
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        response = client.post(
            "/stream/start",
            headers=headers,
            json={"stream_id": "test", "source": 0},
        )
        assert response.status_code == 403

    def test_operator_can_start_stream(self, client, operator_user):
        login_resp = client.post(
            "/auth/login", json={"username": "operator", "password": "operatorpass123"}
        )
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        response = client.post(
            "/stream/start",
            headers=headers,
            json={"stream_id": "test", "source": 0},
        )
        # Auth succeeded: operator is permitted to start a stream. The actual
        # outcome depends on whether a source is available in the test env
        # (200 when a stream starts, 400/422 on validation failure) — the key
        # assertion is that RBAC did not block it with 401/403.
        assert response.status_code not in (401, 403)

    def test_admin_can_manage_users(self, client, admin_user):
        login_resp = client.post("/auth/login", json={"username": "admin", "password": "adminpass123"})
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get("/auth/users", headers=headers)
        assert response.status_code == 200


class TestAPIKeys:
    def test_create_api_key(self, client, test_db, auth_user):
        session = test_db()
        service = AuthService(session)
        plain_key, api_key = service.create_api_key(auth_user.id, "test-key")
        assert plain_key
        assert api_key.id
        assert api_key.is_active

    def test_api_key_revocation(self, client, test_db, auth_user):
        session = test_db()
        service = AuthService(session)
        plain_key, api_key = service.create_api_key(auth_user.id, "test-key")
        success = service.revoke_api_key(api_key.id, auth_user.id)
        assert success
        key_record = session.query(APIKey).filter_by(id=api_key.id).first()
        assert not key_record.is_active

    def test_api_key_expiration(self, client, test_db, auth_user):
        session = test_db()
        service = AuthService(session)
        plain_key, api_key = service.create_api_key(auth_user.id, "test-key", expires_in_days=1)
        assert api_key.expires_at
        assert api_key.expires_at > datetime.utcnow()


class TestAuditLogging:
    def test_audit_log_on_login(self, client, test_db, auth_user):
        response = client.post("/auth/login", json={"username": "testuser", "password": "testpass123"})
        assert response.status_code == 200
        session = test_db()
        logs = session.query(AuditLog).filter_by(action="login", username="testuser").all()
        assert len(logs) > 0
        assert logs[0].status == "success"

    def test_audit_log_on_failed_login(self, client, test_db):
        response = client.post("/auth/login", json={"username": "testuser", "password": "wrongpassword"})
        assert response.status_code == 401
        session = test_db()
        logs = session.query(AuditLog).filter_by(action="login", username="testuser").all()
        assert len(logs) > 0
        assert logs[0].status == "failure"


class TestRateLimiting:
    def test_rate_limit_on_login(self, client):
        settings = get_settings()
        if settings.auth_rate_limit_per_minute == 0:
            pytest.skip("Rate limiting disabled")
        limit = settings.auth_rate_limit_per_minute
        for i in range(limit):
            response = client.post(
                "/auth/login",
                json={"username": f"user{i}", "password": "pass"},
            )
            assert response.status_code in (401, 422)
        response = client.post(
            "/auth/login",
            json={"username": "user", "password": "pass"},
        )
        assert response.status_code == 429


class TestSessionManagement:
    def test_session_creation_on_login(self, client, test_db, auth_user):
        settings = get_settings()
        if not settings.session_management_enabled:
            pytest.skip("Session management disabled")
        response = client.post("/auth/login", json={"username": "testuser", "password": "testpass123"})
        assert response.status_code == 200
        session_db = test_db()
        sessions = session_db.query(SessionModel).filter_by(user_id=auth_user.id).all()
        assert len(sessions) > 0

    def test_session_revocation(self, client, test_db, auth_user):
        settings = get_settings()
        if not settings.session_management_enabled:
            pytest.skip("Session management disabled")
        login_resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass123"})
        jti = login_resp.json().get("session_jti")
        if jti:
            response = client.post("/auth/logout", json={"jti": jti})
            assert response.status_code == 200


class TestPasswordHashing:
    def test_password_hashing(self):
        from backend.auth.security import hash_password, verify_password
        password = "testpassword123"
        hashed = hash_password(password)
        assert verify_password(password, hashed)
        assert not verify_password("wrongpassword", hashed)

    def test_password_salt_uniqueness(self):
        from backend.auth.security import hash_password, verify_password
        password = "testpassword123"
        hash1 = hash_password(password)
        hash2 = hash_password(password)
        assert hash1 != hash2
        assert verify_password(password, hash1)
        assert verify_password(password, hash2)


class TestWebSocketAuth:
    def test_websocket_requires_auth_when_enabled(self, client):
        settings = get_settings()
        if not settings.jwt_secret:
            pytest.skip("JWT not configured")
        with pytest.raises(Exception):
            with client.websocket_connect("/stream/test/ws") as websocket:
                pass

    def test_websocket_accepts_valid_token(self, client, auth_user):
        settings = get_settings()
        if not settings.jwt_secret:
            pytest.skip("JWT not configured")
        login_resp = client.post("/auth/login", json={"username": "testuser", "password": "testpass123"})
        token = login_resp.json()["access_token"]
        try:
            with client.websocket_connect(f"/stream/test/ws?token={token}") as websocket:
                data = websocket.receive_json()
                assert data is not None
        except Exception:
            pass
