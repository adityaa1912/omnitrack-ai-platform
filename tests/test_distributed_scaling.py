"""Distributed multi-replica scaling tests: lease acquisition, expiry,
heartbeat, release, reassignment, duplicate-processing prevention, Redis
failure, shutdown, instance identities, and WebSocket ownership behavior."""

from __future__ import annotations

import threading
import time

import pytest

from backend.ownership import LeaseManager, LeaderLease, StreamLease
from backend.service import InferenceService, StreamConfig

from distributed_fake_redis import FakeClock, FakeRedis


def _manager(client, instance_id, **kwargs):
    defaults = dict(
        ttl_seconds=15.0,
        heartbeat_interval_seconds=5.0,
        acquire_timeout_seconds=0.0,
    )
    defaults.update(kwargs)
    return LeaseManager(client, instance_id=instance_id, **defaults)


class TestLeaseAcquisition:
    def test_exactly_one_winner_under_concurrency(self):
        client = FakeRedis()
        managers = [_manager(client, f"replica-{i}") for i in range(5)]
        results = []
        barrier = threading.Barrier(5)

        def attempt(m):
            barrier.wait()
            results.append(m.acquire("cam-1") is not None)

        threads = [threading.Thread(target=attempt, args=(m,)) for m in managers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(results) == 1
        for m in managers:
            m.stop(grace_seconds=1)

    def test_acquire_after_release(self):
        client = FakeRedis()
        m1 = _manager(client, "r1")
        m2 = _manager(client, "r2")
        assert m1.acquire("s") is not None
        assert m2.acquire("s") is None
        assert m1.release("s") is True
        assert m2.acquire("s") is not None
        m1.stop(grace_seconds=1)
        m2.stop(grace_seconds=1)

    def test_reacquire_locally_returns_same_lease(self):
        client = FakeRedis()
        m = _manager(client, "r1")
        lease = m.acquire("s")
        assert m.acquire("s") is lease
        m.stop(grace_seconds=1)


class TestLeaseExpiry:
    def test_expired_lease_can_be_taken_over(self):
        clock = FakeClock()
        client = FakeRedis(clock=clock)
        m1 = _manager(client, "r1", ttl_seconds=10)
        assert m1.acquire("cam") is not None
        clock.advance(11)
        m2 = _manager(client, "r2")
        assert m2.acquire("cam") is not None
        assert m2.owner_of("cam") == "r2"
        m1.stop(grace_seconds=1)
        m2.stop(grace_seconds=1)

    def test_heartbeat_prevents_expiry(self):
        clock = FakeClock()
        client = FakeRedis(clock=clock)
        m = _manager(client, "r1", ttl_seconds=10)
        m.acquire("cam")
        for _ in range(5):
            clock.advance(4)
            assert m.heartbeat_all() == 1
        clock.advance(4)
        assert m.owner_of("cam") == "r1"
        m.stop(grace_seconds=1)


class TestHeartbeatLoss:
    def test_heartbeat_detects_foreign_owner(self):
        client = FakeRedis()
        m1 = _manager(client, "r1")
        lost = []
        m1 = LeaseManager(
            client,
            instance_id="r1",
            ttl_seconds=15.0,
            heartbeat_interval_seconds=5.0,
            acquire_timeout_seconds=0.0,
            on_lost=lost.append,
        )
        m1.acquire("cam")
        client.set("omnitrack:lease:cam", "r2", ex=60)
        assert m1.heartbeat_all() == 0
        assert lost == ["cam"]
        assert client.get("omnitrack:lease:cam") == "r2"

    def test_heartbeat_after_expiry_marks_lost(self):
        clock = FakeClock()
        client = FakeRedis(clock=clock)
        lost = []
        m = LeaseManager(
            client,
            instance_id="r1",
            ttl_seconds=5.0,
            heartbeat_interval_seconds=1.0,
            acquire_timeout_seconds=0.0,
            on_lost=lost.append,
        )
        m.acquire("cam")
        clock.advance(6)
        assert m.heartbeat_all() == 0
        assert lost == ["cam"]

    def test_heartbeat_survives_owner_recovery_after_expiry(self):
        clock = FakeClock()
        client = FakeRedis(clock=clock)
        m = _manager(client, "r1", ttl_seconds=5)
        m.acquire("cam")
        clock.advance(6)
        m.heartbeat_all()
        m2 = _manager(client, "r2")
        lease = m2.acquire("cam")
        assert lease is not None
        assert m2.owner_of("cam") == "r2"


class TestRelease:
    def test_release_only_by_owner(self):
        client = FakeRedis()
        m1 = _manager(client, "r1")
        m2 = _manager(client, "r2")
        m1.acquire("s")
        assert m2.release("s") is False
        assert client.get("omnitrack:lease:s") == "r1"
        assert m1.release("s") is True
        assert client.get("omnitrack:lease:s") is None

    def test_release_after_takeover_is_safe(self):
        client = FakeRedis()
        m1 = _manager(client, "r1", ttl_seconds=1)
        m1.acquire("s")
        client.set("omnitrack:lease:s", "r2", ex=60)
        assert m1.release("s") is False
        assert client.get("omnitrack:lease:s") == "r2"

    def test_stop_releases_all(self):
        client = FakeRedis()
        m = _manager(client, "r1")
        m.acquire("a")
        m.acquire("b")
        released = m.stop(grace_seconds=1)
        assert released == 2
        assert m.owner_of("a") is None
        assert m.owner_of("b") is None


class TestRedisFailure:
    def test_acquire_fails_safe_on_redis_error(self):
        client = FakeRedis()
        m = _manager(client, "r1")
        client.fail_mode = True
        assert m.acquire("s") is None

    def test_heartbeat_fails_safe_without_losing_lease(self):
        client = FakeRedis()
        m = _manager(client, "r1")
        m.acquire("s")
        client.fail_mode = True
        assert m.heartbeat_all() == 0
        client.fail_mode = False
        assert m.owner_of("s") == "r1"
        client.fail_mode = False
        assert m.heartbeat_all() == 1

    def test_release_failure_is_reported_not_raised(self):
        client = FakeRedis()
        m = _manager(client, "r1")
        m.acquire("s")
        client.fail_mode = True
        assert m.release("s") is False


class TestInstanceIdentity:
    def test_distinct_instances_never_share_id(self):
        client = FakeRedis()
        m1 = _manager(client, None)
        m2 = _manager(client, None)
        assert m1.instance_id != m2.instance_id
        m1.stop(grace_seconds=1)
        m2.stop(grace_seconds=1)

    def test_explicit_instance_id_is_honored(self):
        client = FakeRedis()
        m = _manager(client, "pod-42")
        assert m.instance_id == "pod-42"
        m.stop(grace_seconds=1)


class TestLeaderLease:
    def test_single_leader_per_cycle(self):
        client = FakeRedis()
        l1 = LeaderLease(client, "analytics", "r1", ttl_seconds=60)
        l2 = LeaderLease(client, "analytics", "r2", ttl_seconds=60)
        claims = [l1.claim(), l2.claim(), l1.claim()]
        assert claims.count(True) == 1

    def test_leadership_rotates_after_ttl(self):
        clock = FakeClock()
        client = FakeRedis(clock=clock)
        l1 = LeaderLease(client, "expiry", "r1", ttl_seconds=10)
        l2 = LeaderLease(client, "expiry", "r2", ttl_seconds=10)
        assert l1.claim() is True
        assert l2.claim() is False
        clock.advance(11)
        assert l2.claim() is True

    def test_redis_failure_fails_safe_to_claim(self):
        client = FakeRedis()
        l = LeaderLease(client, "x", "r1", ttl_seconds=10)
        client.fail_mode = True
        assert l.claim() is True


class TestServiceOwnership:
    def _service(self):
        return InferenceService(db_path="sqlite://")

    def test_start_rejected_when_lease_held_elsewhere(self):
        service = self._service()
        client = FakeRedis()
        other = _manager(client, "other-replica")
        other.acquire("cam-owned")
        service.set_lease_manager(_manager(client, "self-replica"))
        with pytest.raises(ValueError, match="already owned"):
            service.start_stream(
                StreamConfig(stream_id="cam-owned", source=0)
            )
        other.stop(grace_seconds=1)

    def test_stream_owner_reflects_lease_table(self):
        service = self._service()
        client = FakeRedis()
        service.set_lease_manager(_manager(client, "self"))
        other = _manager(client, "other")
        other.acquire("cam-x")
        assert service.stream_owner("cam-x") == "other"
        assert service.stream_owner("cam-unknown") is None
        other.stop(grace_seconds=1)

    def test_local_owner_when_no_lease_manager(self):
        service = self._service()
        assert service.stream_owner("nope") is None


class TestWebSocketOwnership:
    def test_owner_redirect_message_shape(self):
        from backend.main import _owner_redirect_payload

        payload = _owner_redirect_payload("cam-1", "pod-7")
        assert payload == {
            "type": "owner_redirect",
            "stream_id": "cam-1",
            "owner": "pod-7",
        }

    def test_no_redirect_for_local_owner_or_unknown(self):
        from backend.main import _should_redirect

        assert _should_redirect(owner=None, local="pod-7") is False
        assert _should_redirect(owner="pod-7", local="pod-7") is False
        assert _should_redirect(owner="pod-9", local="pod-7") is True


class TestShutdown:
    def test_stop_is_idempotent(self):
        client = FakeRedis()
        m = _manager(client, "r1")
        m.acquire("s")
        assert m.stop(grace_seconds=1) == 1
        assert m.stop(grace_seconds=1) == 0

    def test_heartbeat_thread_stops(self):
        client = FakeRedis()
        m = LeaseManager(
            client,
            instance_id="r1",
            ttl_seconds=15.0,
            heartbeat_interval_seconds=0.05,
            acquire_timeout_seconds=0.0,
        )
        m.acquire("s")
        thread = m._thread
        assert thread is not None and thread.is_alive()
        m.stop(grace_seconds=2)
        assert not thread.is_alive()
