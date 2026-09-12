import os
os.environ.setdefault("OMNITRACK_JWT_SECRET", "test-jwt-secret-min-32-chars-long!")

import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main
from backend.main import app
from backend.service import InferenceService, StreamConfig


def test_start_stream_rejected_after_shutdown():
    service = InferenceService(db_path="sqlite://")
    service.shutdown()
    with pytest.raises(RuntimeError, match="shutting down"):
        service.start_stream(StreamConfig(stream_id="late", source=0))


def test_shutdown_idempotent_service_level():
    service = InferenceService(db_path="sqlite://")
    service.shutdown()
    service.shutdown()
    assert service._is_shutdown is True


def test_lifespan_shutdown_tolerates_step_failure_and_stops_everything(monkeypatch):
    recorded = []

    def service_shutdown_raiser():
        recorded.append("streams")
        raise RuntimeError("simulated shutdown failure")

    class FakeAlertEngine:
        def stop(self):
            recorded.append("alert_engine")

    class FakeAlertManager:
        def stop(self):
            recorded.append("alert_manager")

    class FakeAnalyticsAggregator:
        def __init__(self):
            self.stop_calls = 0

        def stop(self):
            self.stop_calls += 1
            recorded.append("analytics_aggregator")

    class FakeLeaseManager:
        def stop(self, grace_seconds=5.0):
            recorded.append("lease_manager")

    fake_agg = FakeAnalyticsAggregator()
    monkeypatch.setattr(backend_main.service, "shutdown", service_shutdown_raiser)
    monkeypatch.setattr(backend_main, "alert_engine", FakeAlertEngine())
    monkeypatch.setattr(backend_main, "alert_manager", FakeAlertManager())
    monkeypatch.setattr(backend_main, "analytics_aggregator", fake_agg)
    monkeypatch.setattr(backend_main, "lease_manager", FakeLeaseManager())

    with TestClient(app) as client:
        assert client is not None

    expected = ["streams", "alert_engine", "alert_manager", "analytics_aggregator", "lease_manager"]
    it = iter(recorded)
    assert all(name in it for name in expected)
    assert fake_agg.stop_calls == 1
