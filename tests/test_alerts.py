import os
os.environ["OMNITRACK_JWT_SECRET"] = "test-jwt-secret-min-32-chars-long!"

import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, User
from backend.auth.dependencies import configure_get_db
from backend.auth.security import encode_jwt, hash_password
from backend.settings import get_settings

from backend.alerts import rules
from backend.alerts.rules import compare, evaluate_condition, evaluate_rule
from backend.alerts.state import AlertStateStore
from backend.alerts.notifications import (
    InAppNotificationProvider,
    NotificationDispatcher,
    NotificationProvider,
    WebhookNotificationProvider,
)
from backend.alerts.publisher import AlertEventPublisher
from backend.alerts.manager import AlertManager, AlertTransitionError
from backend.alerts.engine import AlertRuleEngine
from backend.alerts.models import (
    AlertInstance,
    AlertRule,
    AlertStateHistory,
    NotificationAttempt,
)
from backend.alerts.router import router as alerts_router


def _snapshot(**kwargs):
    base = {
        "object_counts": {},
        "zone_occupancy": {},
        "line_crossings": {},
        "dwell_time_total_seconds": 0.0,
    }
    base.update(kwargs)
    return base


def _event(stream_id="cam1", event_type="OBJECT_APPEARED", class_name=None, metadata=None):
    return {
        "stream_id": stream_id,
        "event_type": event_type,
        "class_name": class_name,
        "track_id": 1,
        "metadata": metadata or {},
        "timestamp": time.time(),
    }


@pytest.fixture
def db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = tmp.name
    tmp.close()
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    yield Session
    engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def manager(db):
    state = AlertStateStore(None, dedup_window_seconds=300)
    dispatcher = NotificationDispatcher([], db, max_retries=0, retry_delay_seconds=0)
    publisher = AlertEventPublisher(None, "alerts")
    m = AlertManager(db, state, publisher, dispatcher, expiry_interval_seconds=1)
    yield m
    m.stop()


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------

class TestRuleEvaluation:
    def test_compare_operators(self):
        assert compare(5, "gte", 5)
        assert compare(6, "gt", 5)
        assert compare(4, "lt", 5)
        assert compare(5, "lte", 5)
        assert compare(5, "eq", 5)
        assert compare(5, "ne", 6)
        assert not compare(4, "gte", 5)

    def test_object_count_threshold(self):
        snap = _snapshot(object_counts={"person": 7})
        cond = {"type": rules.OBJECT_COUNT_THRESHOLD, "operator": "gte", "threshold": 5, "class_name": "person"}
        matched, value = evaluate_condition(cond, snap, None)
        assert matched and value == 7

    def test_object_count_below_threshold(self):
        snap = _snapshot(object_counts={"person": 2})
        cond = {"type": rules.OBJECT_COUNT_THRESHOLD, "operator": "gte", "threshold": 5, "class_name": "person"}
        matched, _ = evaluate_condition(cond, snap, None)
        assert not matched

    def test_zone_occupancy_threshold(self):
        snap = _snapshot(zone_occupancy={"lobby": {"entries": 10, "exits": 3}})
        cond = {"type": rules.ZONE_OCCUPANCY_THRESHOLD, "operator": "gte", "threshold": 5, "zone_name": "lobby"}
        matched, value = evaluate_condition(cond, snap, None)
        assert matched and value == 7

    def test_dwell_time_threshold_from_event(self):
        cond = {"type": rules.DWELL_TIME_THRESHOLD, "operator": "gt", "threshold": 10}
        event = _event(event_type="DWELL_TIME", metadata={"seconds": 15.0})
        matched, value = evaluate_condition(cond, _snapshot(), event)
        assert matched and value == 15.0

    def test_line_crossing_threshold(self):
        snap = _snapshot(line_crossings={"gate": {"positive": 4, "negative": 2}})
        cond = {"type": rules.LINE_CROSSING_THRESHOLD, "operator": "gte", "threshold": 5, "line_name": "gate"}
        matched, value = evaluate_condition(cond, snap, None)
        assert matched and value == 6

    def test_event_type_match(self):
        cond = {"type": rules.EVENT_TYPE_MATCH, "event_type": "NEAR_COLLISION"}
        assert evaluate_condition(cond, _snapshot(), _event(event_type="NEAR_COLLISION"))[0]
        assert not evaluate_condition(cond, _snapshot(), _event(event_type="OBJECT_APPEARED"))[0]

    def test_class_specific(self):
        cond = {"type": rules.CLASS_SPECIFIC, "class_name": "car"}
        assert evaluate_condition(cond, _snapshot(), _event(class_name="car"))[0]
        assert not evaluate_condition(cond, _snapshot(), _event(class_name="person"))[0]

    def test_and_logic(self):
        snap = _snapshot(object_counts={"person": 7})
        conditions = [
            {"type": rules.OBJECT_COUNT_THRESHOLD, "operator": "gte", "threshold": 5, "class_name": "person"},
            {"type": rules.EVENT_TYPE_MATCH, "event_type": "OBJECT_APPEARED"},
        ]
        assert evaluate_rule(conditions, "AND", snap, _event())["matched"]
        conditions[1]["event_type"] = "NEAR_COLLISION"
        assert not evaluate_rule(conditions, "AND", snap, _event())["matched"]

    def test_or_logic(self):
        snap = _snapshot(object_counts={"person": 1})
        conditions = [
            {"type": rules.OBJECT_COUNT_THRESHOLD, "operator": "gte", "threshold": 5, "class_name": "person"},
            {"type": rules.EVENT_TYPE_MATCH, "event_type": "OBJECT_APPEARED"},
        ]
        assert evaluate_rule(conditions, "OR", snap, _event())["matched"]

    def test_empty_conditions_no_match(self):
        assert not evaluate_rule([], "AND", _snapshot(), None)["matched"]


# ---------------------------------------------------------------------------
# Redis / in-process state (cooldown + dedup)
# ---------------------------------------------------------------------------

class TestStateStore:
    def test_cooldown(self):
        store = AlertStateStore(None)
        assert not store.in_cooldown(1, "k")
        store.set_cooldown(1, "k", 60)
        assert store.in_cooldown(1, "k")

    def test_cooldown_zero_is_noop(self):
        store = AlertStateStore(None)
        store.set_cooldown(1, "k", 0)
        assert not store.in_cooldown(1, "k")

    def test_dedup(self):
        store = AlertStateStore(None, dedup_window_seconds=60)
        assert not store.is_duplicate(1, "k")
        store.mark_seen(1, "k")
        assert store.is_duplicate(1, "k")

    def test_cooldown_expiry(self):
        store = AlertStateStore(None)
        store.set_cooldown(1, "k", 1)
        assert store.in_cooldown(1, "k")
        time.sleep(1.1)
        assert not store.in_cooldown(1, "k")

    def test_redis_hot_cache_used(self):
        from unittest.mock import MagicMock
        cache = MagicMock()
        store = AlertStateStore(cache, dedup_window_seconds=60)
        store.cache_active("cam1", [{"id": 1}])
        cache.set.assert_called()


# ---------------------------------------------------------------------------
# Notifications (providers, dispatcher, retry, failure isolation)
# ---------------------------------------------------------------------------

class _Failing(NotificationProvider):
    name = "failing"

    def __init__(self):
        self.calls = 0

    def send(self, payload):
        self.calls += 1
        raise RuntimeError("boom")


class TestNotifications:
    def test_inapp_delivery(self, db):
        received = []
        provider = InAppNotificationProvider(sink=received.append)
        dispatcher = NotificationDispatcher([provider], db, max_retries=0)
        dispatcher.dispatch(1, {"alert_id": 1})
        assert received == [{"alert_id": 1}]
        session = db()
        rows = session.query(NotificationAttempt).filter_by(status="success").all()
        session.close()
        assert len(rows) == 1

    def test_retry_then_fail(self, db):
        provider = _Failing()
        dispatcher = NotificationDispatcher([provider], db, max_retries=2, retry_delay_seconds=0)
        dispatcher.dispatch(1, {"alert_id": 1})
        assert provider.calls == 3
        session = db()
        failed = session.query(NotificationAttempt).filter_by(status="failed").count()
        retrying = session.query(NotificationAttempt).filter_by(status="retrying").count()
        session.close()
        assert failed == 1
        assert retrying == 2

    def test_failure_isolation(self, db):
        received = []
        good = InAppNotificationProvider(sink=received.append)
        dispatcher = NotificationDispatcher([_Failing(), good], db, max_retries=0)
        dispatcher.dispatch(1, {"alert_id": 1})
        assert received == [{"alert_id": 1}]

    def test_webhook_provider_raises_on_bad_url(self):
        provider = WebhookNotificationProvider("http://127.0.0.1:0/none", timeout_seconds=0.2)
        with pytest.raises(Exception):
            provider.send({"a": 1})


# ---------------------------------------------------------------------------
# Kafka publisher
# ---------------------------------------------------------------------------

class TestPublisher:
    def test_disabled(self):
        pub = AlertEventPublisher(None, "alerts")
        assert pub.enabled is False
        assert pub.publish_lifecycle("cam1", 1, "triggered", "warning") is False

    def test_enabled_publishes(self):
        from unittest.mock import MagicMock
        producer = MagicMock()
        producer.publish.return_value = True
        base = MagicMock()
        base.enabled = True
        base._producer = producer
        pub = AlertEventPublisher(base, "alerts.test")
        assert pub.publish_lifecycle("cam1", 7, "resolved", "critical", {"x": 1}) is True
        producer.publish.assert_called_once()
        assert producer.publish.call_args[0][0] == "alerts.test"


# ---------------------------------------------------------------------------
# Manager lifecycle + persistence + dedup + expiry + concurrency + shutdown
# ---------------------------------------------------------------------------

def _rule(rule_id=1, **kwargs):
    base = {"id": rule_id, "name": "r", "severity": "warning", "priority": 0, "cooldown_seconds": 60}
    base.update(kwargs)
    return base


class TestManager:
    def test_trigger_persists(self, manager, db):
        alert_id = manager.trigger(_rule(dedup_key="d1"), {"count": 6}, "cam1", _event())
        assert alert_id is not None
        session = db()
        inst = session.query(AlertInstance).filter_by(id=alert_id).first()
        history = session.query(AlertStateHistory).filter_by(alert_instance_id=alert_id).count()
        session.close()
        assert inst.state == "triggered"
        assert history == 1

    def test_trigger_dedup(self, manager, db):
        first = manager.trigger(_rule(dedup_key="d1"), {}, "cam1", _event())
        second = manager.trigger(_rule(dedup_key="d1"), {}, "cam1", _event())
        assert first == second
        session = db()
        count = session.query(AlertInstance).count()
        session.close()
        assert count == 1

    def test_acknowledge(self, manager, db):
        alert_id = manager.trigger(_rule(dedup_key="d1"), {}, "cam1", _event())
        result = manager.acknowledge(alert_id, "operator1")
        assert result["state"] == "acknowledged"
        session = db()
        inst = session.query(AlertInstance).filter_by(id=alert_id).first()
        session.close()
        assert inst.acknowledged_by == "operator1"

    def test_resolve(self, manager, db):
        alert_id = manager.trigger(_rule(dedup_key="d1"), {}, "cam1", _event())
        assert manager.resolve(alert_id, "op")["state"] == "resolved"

    def test_suppress(self, manager, db):
        alert_id = manager.trigger(_rule(dedup_key="d1"), {}, "cam1", _event())
        assert manager.suppress(alert_id, "op", 30)["state"] == "suppressed"
        session = db()
        inst = session.query(AlertInstance).filter_by(id=alert_id).first()
        session.close()
        assert inst.suppressed_until is not None

    def test_invalid_transition(self, manager, db):
        alert_id = manager.trigger(_rule(dedup_key="d1"), {}, "cam1", _event())
        manager.resolve(alert_id, "op")
        with pytest.raises(AlertTransitionError):
            manager.acknowledge(alert_id, "op")

    def test_transition_not_found(self, manager):
        with pytest.raises(AlertTransitionError):
            manager.acknowledge(999999, "op")

    def test_expiry(self, manager, db):
        alert_id = manager.trigger(_rule(dedup_key="d1"), {}, "cam1", _event())
        session = db()
        inst = session.query(AlertInstance).filter_by(id=alert_id).first()
        inst.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        session.commit()
        session.close()
        assert manager.run_expiry() == 1
        session = db()
        inst = session.query(AlertInstance).filter_by(id=alert_id).first()
        session.close()
        assert inst.state == "expired"

    def test_concurrent_triggers(self, manager, db):
        def worker(n):
            manager.trigger(_rule(rule_id=n, dedup_key=f"d{n}"), {}, "cam1", _event())

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        session = db()
        count = session.query(AlertInstance).count()
        session.close()
        assert count == 20

    def test_shutdown_drains(self, db):
        state = AlertStateStore(None)
        dispatcher = NotificationDispatcher([], db, max_retries=0)
        manager = AlertManager(db, state, AlertEventPublisher(None, "a"), dispatcher)
        manager.start()
        manager.trigger(_rule(dedup_key="d1"), {}, "cam1", _event())
        manager.stop()


# ---------------------------------------------------------------------------
# Rule engine (event consumption + cooldown + failure isolation)
# ---------------------------------------------------------------------------

def _add_rule(db, **kwargs):
    session = db()
    rule = AlertRule(
        name=kwargs.get("name", "rule"),
        stream_id=kwargs.get("stream_id"),
        enabled=kwargs.get("enabled", True),
        conditions=kwargs.get("conditions", []),
        condition_logic=kwargs.get("condition_logic", "AND"),
        severity=kwargs.get("severity", "warning"),
        priority=kwargs.get("priority", 0),
        cooldown_seconds=kwargs.get("cooldown_seconds", 60),
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    rid = rule.id
    session.close()
    return rid


class TestEngine:
    def _engine(self, db, manager, analytics=None):
        return AlertRuleEngine(db, analytics, manager, manager._state, rule_cache_ttl_seconds=0)

    def test_evaluate_triggers_alert(self, manager, db):
        _add_rule(db, conditions=[{"type": rules.EVENT_TYPE_MATCH, "event_type": "NEAR_COLLISION"}])
        engine = self._engine(db, manager)
        engine._evaluate(_event(event_type="NEAR_COLLISION"))
        session = db()
        count = session.query(AlertInstance).count()
        session.close()
        assert count == 1

    def test_cooldown_suppresses_duplicate(self, manager, db):
        _add_rule(db, conditions=[{"type": rules.EVENT_TYPE_MATCH, "event_type": "NEAR_COLLISION"}])
        engine = self._engine(db, manager)
        engine._evaluate(_event(event_type="NEAR_COLLISION"))
        engine._evaluate(_event(event_type="NEAR_COLLISION"))
        session = db()
        count = session.query(AlertInstance).count()
        session.close()
        assert count == 1

    def test_no_match_no_alert(self, manager, db):
        _add_rule(db, conditions=[{"type": rules.EVENT_TYPE_MATCH, "event_type": "NEAR_COLLISION"}])
        engine = self._engine(db, manager)
        engine._evaluate(_event(event_type="OBJECT_APPEARED"))
        session = db()
        count = session.query(AlertInstance).count()
        session.close()
        assert count == 0

    def test_handle_event_never_raises(self, manager, db):
        engine = self._engine(db, manager)
        engine.handle_event({"no": "stream"})
        engine.handle_event(None)

    def test_failure_isolation_bad_analytics(self, manager, db):
        from unittest.mock import MagicMock
        analytics = MagicMock()
        analytics.get_current.side_effect = RuntimeError("boom")
        _add_rule(db, conditions=[{"type": rules.EVENT_TYPE_MATCH, "event_type": "NEAR_COLLISION"}])
        engine = self._engine(db, manager, analytics=analytics)
        engine._evaluate(_event(event_type="NEAR_COLLISION"))
        session = db()
        count = session.query(AlertInstance).count()
        session.close()
        assert count == 1

    def test_disabled_rule_ignored(self, manager, db):
        _add_rule(db, enabled=False, conditions=[{"type": rules.EVENT_TYPE_MATCH, "event_type": "NEAR_COLLISION"}])
        engine = self._engine(db, manager)
        engine._evaluate(_event(event_type="NEAR_COLLISION"))
        session = db()
        count = session.query(AlertInstance).count()
        session.close()
        assert count == 0


# ---------------------------------------------------------------------------
# Models sanity
# ---------------------------------------------------------------------------

class TestModels:
    def test_tablenames(self):
        assert AlertRule.__tablename__ == "alert_rules"
        assert AlertInstance.__tablename__ == "alert_instances"
        assert AlertStateHistory.__tablename__ == "alert_state_history"
        assert NotificationAttempt.__tablename__ == "notification_attempts"


# ---------------------------------------------------------------------------
# RBAC-protected REST API
# ---------------------------------------------------------------------------

@pytest.fixture
def api(db):
    get_settings.cache_clear()
    configure_get_db(db)
    state = AlertStateStore(None)
    dispatcher = NotificationDispatcher([], db, max_retries=0)
    manager = AlertManager(db, state, AlertEventPublisher(None, "alerts"), dispatcher)
    engine = AlertRuleEngine(db, None, manager, state, rule_cache_ttl_seconds=0)
    alerts_router.set_manager(engine, manager, None)
    app = FastAPI()
    app.include_router(alerts_router.router)
    return TestClient(app), db, manager


def _make_user(db, role):
    session = db()
    user = User(
        username=role,
        email=f"{role}@example.com",
        password_hash=hash_password("password123"),
        role=role,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    uid = user.id
    session.close()
    return uid


def _headers(uid, role):
    token = encode_jwt({"sub": str(uid), "role": role, "type": "access"}, get_settings().jwt_secret)
    return {"Authorization": f"Bearer {token}"}


_RULE_BODY = {
    "name": "person-overcrowd",
    "stream_id": "cam1",
    "conditions": [
        {"type": "object_count_threshold", "operator": "gte", "threshold": 5, "class_name": "person"}
    ],
    "condition_logic": "AND",
    "severity": "warning",
}


class TestRBAC:
    def test_create_rule_requires_admin(self, api):
        client, db, _ = api
        uid = _make_user(db, "viewer")
        resp = client.post("/alerts/rules", json=_RULE_BODY, headers=_headers(uid, "viewer"))
        assert resp.status_code == 403

    def test_create_rule_no_auth(self, api):
        client, db, _ = api
        resp = client.post("/alerts/rules", json=_RULE_BODY)
        assert resp.status_code == 401

    def test_admin_creates_rule(self, api):
        client, db, _ = api
        uid = _make_user(db, "admin")
        resp = client.post("/alerts/rules", json=_RULE_BODY, headers=_headers(uid, "admin"))
        assert resp.status_code == 201
        assert resp.json()["name"] == "person-overcrowd"

    def test_viewer_lists_rules(self, api):
        client, db, _ = api
        admin = _make_user(db, "admin")
        client.post("/alerts/rules", json=_RULE_BODY, headers=_headers(admin, "admin"))
        viewer = _make_user(db, "viewer")
        resp = client.get("/alerts/rules", headers=_headers(viewer, "viewer"))
        assert resp.status_code == 200
        assert len(resp.json()["rules"]) == 1

    def test_operator_lifecycle(self, api):
        client, db, manager = api
        operator = _make_user(db, "operator")
        alert_id = manager.trigger({"id": 1, "name": "r", "severity": "warning", "priority": 0, "cooldown_seconds": 60, "dedup_key": "d1"}, {}, "cam1", _event())
        ack = client.post(f"/alerts/{alert_id}/acknowledge", json={}, headers=_headers(operator, "operator"))
        assert ack.status_code == 200
        assert ack.json()["state"] == "acknowledged"
        res = client.post(f"/alerts/{alert_id}/resolve", json={}, headers=_headers(operator, "operator"))
        assert res.status_code == 200
        assert res.json()["state"] == "resolved"

    def test_viewer_cannot_acknowledge(self, api):
        client, db, manager = api
        viewer = _make_user(db, "viewer")
        alert_id = manager.trigger({"id": 1, "name": "r", "severity": "warning", "priority": 0, "cooldown_seconds": 60, "dedup_key": "d1"}, {}, "cam1", _event())
        resp = client.post(f"/alerts/{alert_id}/acknowledge", json={}, headers=_headers(viewer, "viewer"))
        assert resp.status_code == 403

    def test_acknowledge_missing_alert(self, api):
        client, db, _ = api
        operator = _make_user(db, "operator")
        resp = client.post("/alerts/999999/acknowledge", json={}, headers=_headers(operator, "operator"))
        assert resp.status_code == 404

    def test_list_active_and_history(self, api):
        client, db, manager = api
        viewer = _make_user(db, "viewer")
        manager.trigger({"id": 1, "name": "r", "severity": "warning", "priority": 0, "cooldown_seconds": 60, "dedup_key": "d1"}, {}, "cam1", _event())
        active = client.get("/alerts/active", headers=_headers(viewer, "viewer"))
        assert active.status_code == 200
        assert len(active.json()["alerts"]) == 1
        history = client.get("/alerts/history", headers=_headers(viewer, "viewer"))
        assert history.status_code == 200
        assert len(history.json()["alerts"]) == 1
