"""Stream lifecycle tests: finished worker threads must remove their stream
from the registry and release its lease immediately, without waiting for a
reaper sweep triggered by an unrelated request."""

from __future__ import annotations

import os
os.environ.setdefault("OMNITRACK_JWT_SECRET", "test-jwt-secret-min-32-chars-long!")

import backend.service
from backend.service import InferenceService, StreamConfig, StreamMetrics
from backend.ownership import LeaseManager

from distributed_fake_redis import FakeRedis


def _manager(client, instance_id, **kwargs):
    defaults = dict(
        ttl_seconds=15.0,
        heartbeat_interval_seconds=5.0,
        acquire_timeout_seconds=0.0,
    )
    defaults.update(kwargs)
    return LeaseManager(client, instance_id=instance_id, **defaults)


class _FinishedStreamStub:
    def __init__(self, stream_id: str) -> None:
        class _Config:
            pass

        self.config = _Config()
        self.config.stream_id = stream_id
        self.is_running = False
        self.thread = None


class TestStreamFinishedCallback:
    def test_finished_stream_removed_from_registry_and_lease_released(self):
        service = InferenceService(db_path="sqlite://")
        client = FakeRedis()
        m = _manager(client, "r1")
        service.set_lease_manager(m)
        sid = "cam-finished"
        stub = _FinishedStreamStub(sid)
        service.streams[sid] = stub
        assert m.acquire(sid) is not None

        service._handle_stream_finished(stub)

        assert sid not in service.streams
        assert service.has_stream(sid) is False
        assert m.owner_of(sid) is None
        assert sid not in m.leases
        m.stop(grace_seconds=1)

    def test_callback_noop_for_replacement_stream(self):
        service = InferenceService(db_path="sqlite://")
        client = FakeRedis()
        m = _manager(client, "r1")
        service.set_lease_manager(m)
        sid = "cam-replaced"
        old_stub = _FinishedStreamStub(sid)
        new_stub = _FinishedStreamStub(sid)
        service.streams[sid] = new_stub
        assert m.acquire(sid) is not None

        service._handle_stream_finished(old_stub)

        assert service.streams.get(sid) is new_stub
        assert m.owner_of(sid) == "r1"
        assert sid in m.leases
        m.stop(grace_seconds=1)

    def test_callback_noop_while_stop_in_progress(self):
        service = InferenceService(db_path="sqlite://")
        sid = "cam-stopping"
        stub = _FinishedStreamStub(sid)
        service.streams[sid] = stub
        service._stopping.add(sid)

        service._handle_stream_finished(stub)

        assert service.streams.get(sid) is stub
        assert sid in service._stopping

    def test_callback_noop_for_unknown_stream(self):
        service = InferenceService(db_path="sqlite://")
        sid = "cam-unknown"
        stub = _FinishedStreamStub(sid)

        service._handle_stream_finished(stub)

        assert service.has_stream(sid) is False

    def test_start_stream_wires_finished_callback(self, monkeypatch):
        service = InferenceService(db_path="sqlite://")
        captured = {}

        class FakeStream:
            def __init__(self, config, session_factory, **kwargs):
                self.config = config
                captured.update(kwargs)

            def start(self):
                pass

            def get_metrics(self):
                return StreamMetrics(stream_id=self.config.stream_id)

        monkeypatch.setattr(backend.service, "InferenceStream", FakeStream)
        service.start_stream(StreamConfig(stream_id="cam-wired", source=0))

        assert captured.get("on_finished") == service._handle_stream_finished
        assert service.has_stream("cam-wired") is True


class TestReapFinished:
    def test_reap_finished_still_works_for_preexisting_streams(self):
        service = InferenceService(db_path="sqlite://")
        sid = "cam-preexisting"
        stub = _FinishedStreamStub(sid)
        service.streams[sid] = stub

        removed = service.reap_finished()

        assert removed == 1
        assert service.has_stream(sid) is False
