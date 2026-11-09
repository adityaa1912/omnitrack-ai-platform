"""Tests for the analytics engine."""

from __future__ import annotations

import time
from collections import namedtuple
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, call

import pytest

from backend.analytics.aggregator import AnalyticsAggregator, _StreamAgg
from backend.analytics.cache import AnalyticsCache
from backend.analytics.publisher import AnalyticsEventPublisher
from backend.analytics.models import AnalyticsSummary, ZoneOccupancy, LineCrossing, TrajectorySnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event(
    stream_id: str = "cam1",
    event_type: str = "object_appeared",
    track_id: Optional[int] = None,
    class_name: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    timestamp: Optional[float] = None,
) -> Dict[str, Any]:
    return {
        "stream_id": stream_id,
        "event_type": event_type,
        "track_id": track_id,
        "class_name": class_name,
        "metadata": metadata or {},
        "timestamp": timestamp or time.time(),
    }


@pytest.fixture
def session_factory():
    """Return a no-op session factory that records calls for the flush test."""
    sessions = []
    def _factory():
        mock = MagicMock()
        mock._calls = sessions
        sessions.append(mock)
        return mock
    return _factory, sessions


@pytest.fixture
def agg(session_factory):
    return AnalyticsAggregator(
        session_factory,
        aggregation_window_seconds=60,
        retention_hours=24,
    )


# ---------------------------------------------------------------------------
# AnalyticsAggregator basic behavior
# ---------------------------------------------------------------------------

class TestAnalyticsAggregator:
    def test_handle_event_unknown_stream(self, agg):
        """Events with no stream_id are dropped silently."""
        record = {"event_type": "OBJECT_APPEARED"}
        agg.handle_event(record)
        assert agg.list_streams() == []

    def test_object_appeared_increments_count(self, agg):
        agg.handle_event(_event(class_name="person"))
        data = agg.get_current("cam1")
        assert data["object_counts"]["person"] == 1

    def test_multiple_classes(self, agg):
        agg.handle_event(_event(class_name="person"))
        agg.handle_event(_event(class_name="car"))
        agg.handle_event(_event(class_name="person"))
        counts = agg.get_object_counts("cam1")
        assert counts["person"] == 2
        assert counts["car"] == 1

    def test_object_disappeared_removes_track(self, agg):
        agg.handle_event(_event(track_id=1, class_name="person"))
        agg.handle_event(_event(event_type="object_disappeared", track_id=1))
        data = agg.get_current("cam1")
        assert data["unique_tracks"] == 0

    def test_object_entered_counts(self, agg):
        agg.handle_event(_event(
            event_type="object_entered",
            track_id=5,
            metadata={"zone": " lobby "},
        ))
        data = agg.get_current("cam1")
        assert data["zone_occupancy"][" lobby "]["entries"] == 1

    def test_object_exited_counts(self, agg):
        now = time.time()
        agg.handle_event(_event(
            event_type="object_entered",
            track_id=10,
            metadata={"zone": "restricted"},
            timestamp=now,
        ))
        agg.handle_event(_event(
            event_type="object_exited",
            track_id=10,
            metadata={"zone": "restricted"},
            timestamp=now + 10.0,
        ))
        data = agg.get_current("cam1")
        zone = data["zone_occupancy"]["restricted"]
        assert zone["exits"] == 1
        assert zone["occupancy_seconds"] >= 10.0
        assert data["dwell_time_total_seconds"] >= 10.0

    def test_line_crossing_direction(self, agg):
        agg.handle_event(_event(
            event_type="crossing_direction",
            track_id=1,
            metadata={"line": "gate", "direction": "positive", "positive_label": "positive"},
        ))
        agg.handle_event(_event(
            event_type="crossing_direction",
            track_id=2,
            metadata={"line": "gate", "direction": "negative", "positive_label": "positive"},
        ))
        crosses = agg.get_line_crossings("cam1")
        assert crosses["gate"]["positive"] == 1
        assert crosses["gate"]["negative"] == 1

    def test_dwelling_time(self, agg):
        agg.handle_event(_event(
            event_type="dwell_time",
            metadata={"zone": "area1", "seconds": 30.0},
        ))
        data = agg.get_current("cam1")
        assert data["dwell_time_total_seconds"] == 30.0
        assert data["zone_occupancy"]["area1"]["dwell_seconds"] == 30.0

    def test_stationary_and_near_collision(self, agg):
        agg.handle_event(_event(event_type="STATIONARY_OBJECT"))
        agg.handle_event(_event(event_type="STATIONARY_OBJECT"))
        agg.handle_event(_event(event_type="NEAR_COLLISION"))
        data = agg.get_current("cam1")
        assert data["stationary_events"] == 2
        assert data["near_collision_events"] == 1

    def test_flush_persists_to_session(self):
        sessions = []
        def _factory():
            mock = MagicMock()
            sessions.append(mock)
            return mock
        agg = AnalyticsAggregator(_factory)
        agg.handle_event(_event(class_name="person"))
        agg.handle_event(_event(class_name="car", track_id=1))
        agg.handle_event(_event(
            event_type="object_entered",
            track_id=1,
            metadata={"zone": "zoneA"},
        ))
        agg.handle_event(_event(
            event_type="object_exited",
            track_id=1,
            metadata={"zone": "zoneA"},
        ))
        agg._flush_once()
        # _flush_once creates exactly one session; verify add/commit were called.
        assert len(sessions) == 1
        mock_session = sessions[0]
        assert mock_session.add.call_count > 0
        assert mock_session.commit.called

    def test_get_object_counts_filter_by_class(self, agg):
        agg.handle_event(_event(class_name="person"))
        agg.handle_event(_event(class_name="car"))
        counts = agg.get_object_counts("cam1", "person")
        assert counts == {"person": 1}
        all_counts = agg.get_object_counts("cam1")
        assert "person" in all_counts
        assert "car" in all_counts

    def test_list_streams(self, agg):
        assert agg.list_streams() == []
        agg.handle_event(_event(stream_id="cam2"))
        assert agg.list_streams() == ["cam2"]


# ---------------------------------------------------------------------------
# _StreamAgg unit tests
# ---------------------------------------------------------------------------

class TestStreamAgg:
    def test_to_dict_shape(self):
        agg = _StreamAgg("cam1", 60)
        agg.handle_event(_event(class_name="dog"))
        d = agg.to_dict()
        assert d["stream_id"] == "cam1"
        assert "object_counts" in d
        assert "unique_tracks" in d
        assert "zone_occupancy" in d
        assert "line_crossings" in d

    def test_zone_stats_with_dwell(self):
        agg = AnalyticsAggregator(lambda: None)
        now = time.time()
        agg.handle_event(_event(
            event_type="object_entered",
            track_id=1,
            metadata={"zone": "kitchen"},
            timestamp=now,
        ))
        agg.handle_event(_event(
            event_type="dwell_time",
            metadata={"zone": "kitchen", "seconds": 12.5},
        ))
        data = agg.get_zone_occupancy("cam1", "kitchen")
        assert data["kitchen"]["dwell_seconds"] == 12.5


# ---------------------------------------------------------------------------
# Task 1 regressions: real producer-format events, flush preservation,
# failed commit, custom line labels, dwell/occupancy across windows.
# ---------------------------------------------------------------------------

class TestProducerFormatRegressions:
    """Feed actual Event.to_dict()-style records (lowercase event_type,
    metadata zone/line/dwell_seconds) and assert counts, zones, dwell."""

    def test_appeared_and_disappeared_counts(self, agg):
        agg.handle_event(_event(event_type="object_appeared", track_id=1, class_name="person"))
        agg.handle_event(_event(event_type="object_appeared", track_id=2, class_name="person"))
        agg.handle_event(_event(event_type="object_disappeared", track_id=1))
        data = agg.get_current("cam1")
        assert data["object_counts"]["person"] == 2
        assert data["unique_tracks"] == 1

    def test_entered_exited_named_zone(self, agg):
        now = time.time()
        agg.handle_event(_event(
            event_type="object_entered", track_id=1, class_name="person",
            metadata={"zone": "lobby"}, timestamp=now,
        ))
        agg.handle_event(_event(
            event_type="object_exited", track_id=1, class_name="person",
            metadata={"zone": "lobby"}, timestamp=now + 10.0,
        ))
        data = agg.get_current("cam1")
        zone = data["zone_occupancy"]["lobby"]
        assert zone["entries"] == 1
        assert zone["exits"] == 1
        assert zone["occupancy_seconds"] >= 10.0
        assert data["dwell_time_total_seconds"] >= 10.0

    def test_line_crossing_custom_labels(self, agg):
        agg.handle_event(_event(
            event_type="crossing_direction", track_id=1,
            metadata={"line": "gate", "direction": "in", "positive_label": "in"},
        ))
        agg.handle_event(_event(
            event_type="crossing_direction", track_id=2,
            metadata={"line": "gate", "direction": "out", "positive_label": "in"},
        ))
        crosses = agg.get_line_crossings("cam1")
        assert crosses["gate"]["positive"] == 1   # "in" == positive_label
        assert crosses["gate"]["negative"] == 1   # "out" != positive_label

    def test_dwell_time_threshold_record(self, agg):
        agg.handle_event(_event(
            event_type="dwell_time",
            metadata={"zone": "area1", "seconds": 30.0, "threshold_seconds": 5.0},
        ))
        data = agg.get_current("cam1")
        assert data["dwell_time_total_seconds"] == 30.0
        assert data["zone_occupancy"]["area1"]["dwell_seconds"] == 30.0

    def test_custom_labels_backward_compatible_default_positive(self, agg):
        # No positive_label metadata: defaults to "positive".
        agg.handle_event(_event(
            event_type="crossing_direction", track_id=1,
            metadata={"line": "gate", "direction": "positive"},
        ))
        assert agg.get_line_crossings("cam1")["gate"]["positive"] == 1


class TestFlushPreservation:
    def _session(self):
        s = MagicMock()
        s.commit.side_effect = None
        return s

    def _agg_stream(self, agg):
        return agg._per_stream["cam1"]

    def test_active_zone_membership_survives_flush(self, agg):
        """A track inside a zone at flush time remains counted after flush."""
        now = time.time()
        agg.handle_event(_event(event_type="object_appeared", track_id=1, class_name="person"))
        agg.handle_event(_event(
            event_type="object_entered", track_id=1, class_name="person",
            metadata={"zone": "lobby"}, timestamp=now,
        ))
        before = agg.get_current("cam1")
        assert before["zone_occupancy"]["lobby"]["entries"] == 1
        self._agg_stream(agg).flush(lambda: self._session())
        after = agg.get_current("cam1")
        # Track still present after flush (not dropped).
        assert after["unique_tracks"] == 1

    def test_dwell_occupancy_across_windows_and_disappearance(self, agg):
        now = time.time()
        agg.handle_event(_event(event_type="object_appeared", track_id=1, class_name="person"))
        agg.handle_event(_event(
            event_type="object_entered", track_id=1, class_name="person",
            metadata={"zone": "lobby"}, timestamp=now,
        ))
        agg.handle_event(_event(
            event_type="object_exited", track_id=1, class_name="person",
            metadata={"zone": "lobby"}, timestamp=now + 5.0,
        ))
        # Snapshot pre-flush counters (window stats are reset by flush).
        pre = self._agg_stream(agg).get_zone_occupancy("lobby")
        assert pre["lobby"]["entries"] == 1
        assert pre["lobby"]["exits"] == 1
        self._agg_stream(agg).flush(lambda: self._session())
        # Track remains a live member after flush (occupancy continues).
        assert len(self._agg_stream(agg)._tracks) == 1

    def test_failed_commit_retains_data_exactly_once(self, agg):
        """A failed DB commit keeps in-memory data intact (retry flushes once)."""
        class FailingSession:
            def add(self, obj): pass
            def commit(self): raise RuntimeError("db down")
            def rollback(self): pass
            def close(self): pass
        agg.handle_event(_event(event_type="object_appeared", track_id=1, class_name="car"))
        assert agg.get_current("cam1")["object_counts"]["car"] == 1
        self._agg_stream(agg).flush(lambda: FailingSession())  # flush must not raise / lose data
        assert agg.get_current("cam1")["object_counts"]["car"] == 1
        # Retry flush after the failure succeeds and clears the window (data
        # counted exactly once across the two flush attempts).
        self._agg_stream(agg).flush(lambda: self._session())

    def test_manual_flush_succeeds_and_closes_session(self, agg):
        sessions = []
        class CountingSession:
            def __init__(self):
                sessions.append(self)
                self.closed = False
                self.committed = False
            def add(self, obj): pass
            def commit(self): self.committed = True
            def rollback(self): pass
            def close(self): self.closed = True
        agg.handle_event(_event(event_type="object_appeared", track_id=1, class_name="car"))
        agg._per_stream["cam1"].flush(lambda: CountingSession())
        assert len(sessions) == 1
        assert sessions[0].committed
        assert sessions[0].closed

    def test_cached_object_count_equals_uncached(self):
        json_cache = MagicMock()
        json_cache.available = True
        store = {}
        def get(key):
            return store.get(key)
        def set_(key, value, ttl_seconds=None):
            store[key] = value
            return True
        json_cache.get.side_effect = get
        json_cache.set.side_effect = set_
        cache = AnalyticsCache(json_cache)

        agg = AnalyticsAggregator(lambda: None)
        agg.handle_event(_event(event_type="object_appeared", track_id=1, class_name="car"))
        uncached = agg.get_object_counts("cam1")
        # Stored (cached) response equals the uncached response.
        cache.set(uncached, "analytics", "cam1", "objects")
        assert store["analytics:cam1:objects"] == uncached

    def test_flush_takes_session_factory(self, agg):
        """_StreamAgg.flush accepts a session factory callable (not a session)."""
        factory = MagicMock()
        factory.return_value = MagicMock()
        agg.handle_event(_event(event_type="object_appeared", track_id=1, class_name="car"))
        agg._per_stream["cam1"].flush(factory)
        factory.assert_called_once()


# ---------------------------------------------------------------------------
# AnalyticsCache
# ---------------------------------------------------------------------------

class TestAnalyticsCache:
    def test_unavailable_returns_none(self):
        cache = AnalyticsCache(None)
        assert cache.get("key") is None
        assert cache.set("val", "key") is False

    def test_wraps_json_cache(self):
        json_cache = MagicMock()
        json_cache.available = True
        json_cache.get.return_value = "hello"
        cache = AnalyticsCache(json_cache)
        cache.set("hello", "stream1", "data")
        json_cache.set.assert_called_once()
        assert cache.get("stream1", "data") == "hello"


# ---------------------------------------------------------------------------
# AnalyticsEventPublisher
# ---------------------------------------------------------------------------

class TestAnalyticsEventPublisher:
    def test_disabled_returns_false(self):
        pub = AnalyticsEventPublisher(None, "analytics")
        assert pub.enabled is False
        assert pub.publish("cam1", {"data": 1}) is False

    def test_enabled_publishes(self):
        mock_producer = MagicMock()
        mock_producer.publish.return_value = True
        base = MagicMock()
        base.enabled = True
        base._producer = mock_producer
        pub = AnalyticsEventPublisher(base, "analytics.test")
        assert pub.enabled is True
        result = pub.publish("cam1", {"foo": "bar"})
        assert result is True
        mock_producer.publish.assert_called_once()
        assert mock_producer.publish.call_args[0][0] == "analytics.test"
        assert mock_producer.publish.call_args[1]["key"] == "cam1"


# ---------------------------------------------------------------------------
# Analytics models (sanity)
# ---------------------------------------------------------------------------

class TestAnalyticsModels:
    def test_analytics_summary_table_args(self):
        assert AnalyticsSummary.__tablename__ == "analytics_summaries"
        idx_names = {idx.name for idx in AnalyticsSummary.__table__.indexes}
        assert "ix_analytics_summaries_stream_window" in idx_names

    def test_zone_occupancy_table_args(self):
        assert ZoneOccupancy.__tablename__ == "zone_occupancy"

    def test_line_crossing_table_args(self):
        assert LineCrossing.__tablename__ == "line_crossings"

    def test_trajectory_snapshot_table_args(self):
        assert TrajectorySnapshot.__tablename__ == "trajectory_snapshots"
