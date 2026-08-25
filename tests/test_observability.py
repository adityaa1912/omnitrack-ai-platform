import os
os.environ.setdefault("OMNITRACK_JWT_SECRET", "test-jwt-secret-min-32-chars-long!")

import json
import queue
import threading
import time
import pytest

from backend.observability.supervisor import WorkerSupervisor
from backend.observability.config_validator import validate_production_config, safe_effective_config
from backend.observability import correlation
from backend.observability.readiness import CheckResult, ReadinessProbe, ReadinessReport
from backend.observability.errors import ConfigurationError
from backend.settings import Settings


# ────────────────────────────────────────────────────────
# Metrics registration tests
# ────────────────────────────────────────────────────────

# prometheus_client strips the "_total" suffix from Counter metric family names;
# REGISTRY.collect() returns "omnitrack_api_requests", not "omnitrack_api_requests_total".
EXPECTED_METRIC_NAMES = [
    "omnitrack_api_request_latency_seconds",
    "omnitrack_api_requests",
    "omnitrack_api_requests_in_flight",
    "omnitrack_websocket_connections",
    "omnitrack_websocket_connections_opened",
    "omnitrack_websocket_messages_sent",
    "omnitrack_websocket_disconnects",
    "omnitrack_streams_active",
    "omnitrack_streams_started",
    "omnitrack_streams_stopped",
    "omnitrack_stream_errors",
    "omnitrack_stream_lifetime_seconds",
    "omnitrack_worker_health",
    "omnitrack_worker_failures",
    "omnitrack_worker_restarts",
    "omnitrack_worker_queue_depth",
    "omnitrack_dependency_health",
    "omnitrack_analytics_events",
    "omnitrack_alert_rules_evaluated",
    "omnitrack_alerts_triggered",
    "omnitrack_recordings_started",
    "omnitrack_build_info",
]


def test_all_expected_metrics_registered():
    from prometheus_client import REGISTRY
    collected_names = {m.name for m in REGISTRY.collect()}
    for name in EXPECTED_METRIC_NAMES:
        assert name in collected_names, f"Missing metric: {name}"


# ────────────────────────────────────────────────────────
# Cardinality regression tests
# ────────────────────────────────────────────────────────

_UNBOUNDED_LABEL_PATTERNS = ("request_id", "user_id", "camera_id", "event_id", "ip")
_STREAM_BOUNDED_METRICS = {
    "omnitrack_stream_errors",
    "omnitrack_inference_fps",
    "omnitrack_frame_queue_depth",
    "omnitrack_dropped_frames",
    "omnitrack_dropped_events",
}


def test_no_unbounded_labels_on_request_metrics():
    from prometheus_client import REGISTRY
    for metric in REGISTRY.collect():
        if metric.name in (
            "omnitrack_api_requests",
            "omnitrack_api_request_latency_seconds",
            "omnitrack_api_requests_in_flight",
        ):
            for sample in metric.samples:
                for label_name in sample.labels:
                    assert label_name not in _UNBOUNDED_LABEL_PATTERNS, (
                        f"Unbounded label '{label_name}' on {metric.name}"
                    )


def test_worker_metrics_use_only_worker_label():
    from prometheus_client import REGISTRY
    for metric in REGISTRY.collect():
        if metric.name in (
            "omnitrack_worker_health",
            "omnitrack_worker_failures",
            "omnitrack_worker_restarts",
            "omnitrack_worker_queue_depth",
        ):
            for sample in metric.samples:
                for label_name in sample.labels:
                    assert label_name == "worker", (
                        f"Unexpected label '{label_name}' on {metric.name}"
                    )


def test_dependency_health_uses_only_dependency_label():
    from prometheus_client import REGISTRY
    for metric in REGISTRY.collect():
        if metric.name == "omnitrack_dependency_health":
            for sample in metric.samples:
                for label_name in sample.labels:
                    assert label_name == "dependency"


# ────────────────────────────────────────────────────────
# Correlation: user_id
# ────────────────────────────────────────────────────────

def test_bind_and_get_user_id():
    correlation.clear_user_id()
    assert correlation.get_user_id() is None
    correlation.bind_user_id(42)
    assert correlation.get_user_id() == "42"
    correlation.clear_user_id()
    assert correlation.get_user_id() is None


def test_bind_user_id_none():
    correlation.bind_user_id(None)
    assert correlation.get_user_id() is None


def test_user_id_in_log_record(capfd):
    from backend.observability.logging import configure_logging, JsonFormatter
    import logging
    # Use the JsonFormatter directly (not the queue path) to avoid threading complexity
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None,
    )
    correlation.bind_user_id(99)
    output = formatter.format(record)
    correlation.clear_user_id()
    parsed = json.loads(output)
    assert parsed["user_id"] == "99"


def test_user_id_absent_logs_dash(capfd):
    from backend.observability.logging import JsonFormatter
    import logging
    formatter = JsonFormatter()
    correlation.clear_user_id()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hi", args=(), exc_info=None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["user_id"] == "-"


# ────────────────────────────────────────────────────────
# Readiness: degraded state
# ────────────────────────────────────────────────────────

def test_degraded_check_still_passes_ready():
    probe = ReadinessProbe()
    probe.register(lambda: CheckResult(name="opt_service", ok=True, detail="impaired", degraded=True))
    report = probe.evaluate()
    assert report.ready is True
    assert report.degraded is True
    d = report.to_dict()
    assert d["ready"] is True
    assert d["degraded"] is True
    assert d["checks"][0]["degraded"] is True


def test_failing_check_sets_not_ready():
    probe = ReadinessProbe()
    probe.register(lambda: CheckResult(name="db", ok=False, detail="down"))
    report = probe.evaluate()
    assert report.ready is False
    assert report.degraded is False


def test_check_result_to_dict_includes_degraded():
    r = CheckResult(name="foo", ok=True, detail="bar", degraded=True)
    d = r.to_dict()
    assert d == {"name": "foo", "ok": True, "detail": "bar", "degraded": True}


def test_readiness_probe_updates_dependency_health_gauge():
    from backend.observability import metrics as om
    probe = ReadinessProbe()
    probe.register(lambda: CheckResult(name="test_dep_healthy", ok=True, detail="ok"))
    probe.register(lambda: CheckResult(name="test_dep_down", ok=False, detail="gone"))
    probe.evaluate()
    healthy_val = om.DEPENDENCY_HEALTH.labels(dependency="test_dep_healthy")._value.get()
    down_val = om.DEPENDENCY_HEALTH.labels(dependency="test_dep_down")._value.get()
    assert healthy_val == 1.0
    assert down_val == 0.0


# ────────────────────────────────────────────────────────
# Configuration validation
# ────────────────────────────────────────────────────────

def test_dev_config_passes_validation():
    s = Settings(_env_file=None, environment="development")
    validate_production_config(s)  # must not raise


def test_test_config_passes_validation():
    s = Settings(_env_file=None, environment="test")
    validate_production_config(s)


def test_production_without_jwt_fails():
    s = Settings(
        _env_file=None,
        environment="production",
        jwt_secret=None,
        cors_origins="https://example.com",
        postgres_url="postgresql://u:p@db:5432/omnitrack",
    )
    with pytest.raises(ConfigurationError, match="JWT_SECRET"):
        validate_production_config(s)


def test_production_with_wildcard_cors_fails():
    s = Settings(
        _env_file=None,
        environment="production",
        jwt_secret="supersecret-value-long-enough-here!",
        cors_origins="*",
        postgres_url="postgresql://u:p@db:5432/omnitrack",
    )
    with pytest.raises(ConfigurationError, match="CORS"):
        validate_production_config(s)


def test_production_without_postgres_fails():
    s = Settings(
        _env_file=None,
        environment="production",
        jwt_secret="supersecret-value-long-enough-here!",
        cors_origins="https://example.com",
        postgres_url=None,
    )
    with pytest.raises(ConfigurationError, match="POSTGRES"):
        validate_production_config(s)


def test_safe_effective_config_redacts_jwt_secret():
    s = Settings(
        _env_file=None,
        jwt_secret="my-very-secret-key-here-long-enough",
    )
    result = safe_effective_config(s)
    assert result["jwt_secret"] == "***"


def test_safe_effective_config_redacts_api_key():
    s = Settings(_env_file=None, api_key="plain-api-key")
    result = safe_effective_config(s)
    assert result["api_key"] == "***"


def test_safe_effective_config_preserves_non_secret_fields():
    s = Settings(_env_file=None, api_port=9999)
    result = safe_effective_config(s)
    assert result["api_port"] == 9999


# ────────────────────────────────────────────────────────
# Supervisor: registration and health tracking
# ────────────────────────────────────────────────────────

class _FakeWorker:
    def __init__(self, target_fn=None):
        self._running = True
        self._worker = None
        self._target = target_fn or (lambda: None)
        self.start_calls = 0

    def start(self):
        self.start_calls += 1
        self._worker = threading.Thread(target=self._target, daemon=True)
        self._worker.start()


def test_supervisor_registers_worker_with_healthy_gauge():
    from backend.observability import metrics as om
    sup = WorkerSupervisor(check_interval_seconds=100)
    w = _FakeWorker(target_fn=lambda: time.sleep(60))
    w.start()
    sup.register("test_healthy_reg", w, "_worker")
    val = om.WORKER_HEALTH.labels(worker="test_healthy_reg")._value.get()
    assert val == 1.0
    sup.stop()


def test_supervisor_detects_dead_thread_and_increments_failure_counter():
    from backend.observability import metrics as om

    finished = threading.Event()

    def _quick_exit():
        finished.set()

    sup = WorkerSupervisor(check_interval_seconds=0.05)
    w = _FakeWorker(target_fn=_quick_exit)
    w.start()
    finished.wait(timeout=2.0)
    time.sleep(0.05)

    sup.register("test_dead_worker", w, "_worker", max_restarts=0, restart_delay_seconds=0.0)
    sup.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        val = om.WORKER_FAILURES_TOTAL.labels(worker="test_dead_worker")._value.get()
        if val >= 1:
            break
        time.sleep(0.05)

    sup.stop()
    final_val = om.WORKER_FAILURES_TOTAL.labels(worker="test_dead_worker")._value.get()
    assert final_val >= 1


def test_supervisor_restarts_dead_worker():
    from backend.observability import metrics as om

    finished = threading.Event()

    def _quick_exit():
        finished.set()

    sup = WorkerSupervisor(check_interval_seconds=0.05)
    w = _FakeWorker(target_fn=_quick_exit)
    w.start()
    finished.wait(timeout=2.0)
    time.sleep(0.05)

    sup.register("test_restart_worker", w, "_worker", max_restarts=2, restart_delay_seconds=0.0)
    sup.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if om.WORKER_RESTARTS_TOTAL.labels(worker="test_restart_worker")._value.get() >= 1:
            break
        time.sleep(0.05)

    sup.stop()
    assert om.WORKER_RESTARTS_TOTAL.labels(worker="test_restart_worker")._value.get() >= 1
    assert w.start_calls >= 2


def test_supervisor_graceful_shutdown():
    sup = WorkerSupervisor(check_interval_seconds=1.0)
    sup.start()
    t0 = time.monotonic()
    sup.stop()
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0


def test_supervisor_worker_snapshots():
    sup = WorkerSupervisor(check_interval_seconds=100)
    w = _FakeWorker(target_fn=lambda: time.sleep(60))
    w.start()
    sup.register("snap_worker", w, "_worker")
    snaps = sup.worker_snapshots()
    assert len(snaps) == 1
    assert snaps[0]["name"] == "snap_worker"
    assert snaps[0]["alive"] is True
    sup.stop()


# ────────────────────────────────────────────────────────
# App-level endpoint tests
# ────────────────────────────────────────────────────────

import tempfile
import sqlalchemy
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, User
from backend.auth.dependencies import configure_get_db
from backend.auth.security import hash_password
from backend.auth.services import AuthService


@pytest.fixture(scope="module")
def obs_test_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()
    engine = create_engine(f"sqlite:///{tmp_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()
    try:
        os.unlink(tmp_path)
    except OSError:
        pass


@pytest.fixture(scope="module")
def obs_client(obs_test_db):
    # Import app first so main.py's module-level configure_get_db() wiring runs
    # before we override it with the test DB — same pattern as test_auth.py.
    from backend.main import app
    from backend.auth.rate_limit import reset_auth_rate_limiter
    from backend.settings import get_settings
    reset_auth_rate_limiter()
    get_settings.cache_clear()
    configure_get_db(obs_test_db)
    return TestClient(app)


@pytest.fixture(scope="module")
def admin_token(obs_test_db, obs_client):
    session = obs_test_db()
    user = User(
        username="obs_admin",
        email="obs_admin@example.com",
        password_hash=hash_password("adminpass123"),
        role="admin",
        is_active=True,
    )
    session.add(user)
    session.commit()
    resp = obs_client.post("/auth/login", json={"username": "obs_admin", "password": "adminpass123"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def viewer_token(obs_test_db, obs_client):
    session = obs_test_db()
    user = User(
        username="obs_viewer",
        email="obs_viewer@example.com",
        password_hash=hash_password("viewerpass123"),
        role="viewer",
        is_active=True,
    )
    session.add(user)
    session.commit()
    resp = obs_client.post("/auth/login", json={"username": "obs_viewer", "password": "viewerpass123"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def test_liveness_endpoint(obs_client):
    resp = obs_client.get("/live")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "alive"


def test_readiness_endpoint_returns_status(obs_client):
    resp = obs_client.get("/ready")
    assert resp.status_code in (200, 503)
    data = resp.json()
    assert "status" in data
    assert "checks" in data
    assert "degraded" in data


def test_metrics_endpoint_returns_prometheus_text(obs_client):
    resp = obs_client.get("/metrics")
    assert resp.status_code == 200
    assert "omnitrack_" in resp.text
    assert "omnitrack_build_info" in resp.text


def test_diagnostics_requires_admin(obs_client, admin_token):
    resp = obs_client.get(
        "/admin/diagnostics",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "active_streams" in data
    assert "workers" in data
    assert "dependencies" in data
    assert "effective_config" in data


def test_diagnostics_rejects_viewer(obs_client, viewer_token):
    resp = obs_client.get(
        "/admin/diagnostics",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


def test_diagnostics_effective_config_redacts_secrets(obs_client, admin_token):
    resp = obs_client.get(
        "/admin/diagnostics",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    cfg = resp.json()["effective_config"]
    assert cfg.get("jwt_secret") == "***"


def test_readiness_includes_new_checks(obs_client):
    resp = obs_client.get("/ready")
    data = resp.json()
    check_names = {c["name"] for c in data["checks"]}
    assert "recording" in check_names
    assert "analytics" in check_names
    assert "alert_engine" in check_names
