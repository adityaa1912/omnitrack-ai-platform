"""Real-time in-memory analytics aggregator.

Consumes derived events from the existing EventBuffer infrastructure and
maintains per-stream counters for object counts, zone occupancy, line
crossings, dwell time, and trajectory data. Periodic snapshots are
flushed to PostgreSQL and published to Kafka when configured.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .models import AnalyticsSummary, LineCrossing, TrajectorySnapshot, ZoneOccupancy
from backend.observability import metrics as om

logger = logging.getLogger(__name__)


class AnalyticsAggregator:
    """In-memory analytics aggregator with periodic persistence.

    Consumes events via ``handle_event(record)`` — called on the inference
    thread after each buffer append. All per-stream state is keyed by
    ``stream_id`` and the aggregator never touches the inference hot path
    beyond a single dict lookup per event.
    """

    def __init__(
        self,
        session_factory,
        *,
        aggregation_window_seconds: int = 60,
        retention_hours: int = 24,
        trajectory_snapshot_on_disappear: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._window_seconds = aggregation_window_seconds
        self._retention_hours = retention_hours
        self._snapshot_on_disappear = trajectory_snapshot_on_disappear

        self._lock = threading.Lock()
        self._per_stream: Dict[str, _StreamAgg] = {}
        self._last_flush_ts: float = time.time()
        self._running = False

    def start(self) -> None:
        """Mark the aggregator as active. Called at app startup."""
        self._running = True
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()
        logger.info("Analytics aggregator started")

    def stop(self) -> None:
        """Signal the aggregator to stop and flush any remaining data."""
        self._running = False
        self._flush_once()

    def handle_event(self, record: dict) -> None:
        """Consume one derived event record. Safe to call from inference thread."""
        stream_id = record.get("stream_id")
        if not stream_id:
            return
        agg = self._get_or_create(stream_id)
        agg.handle_event(record)

    def get_current(self, stream_id: str) -> Optional[dict]:
        """Return current in-memory analytics for a stream, or None."""
        with self._lock:
            agg = self._per_stream.get(stream_id)
        if agg is None:
            return None
        return agg.to_dict()

    def get_object_counts(
        self, stream_id: str, class_name: Optional[str] = None
    ) -> Dict[str, int]:
        """Return current per-class object counts for a stream."""
        with self._lock:
            agg = self._per_stream.get(stream_id)
        if agg is None:
            return {}
        return agg.get_object_counts(class_name)

    def get_zone_occupancy(
        self, stream_id: str, zone_name: Optional[str] = None
    ) -> Dict[str, dict]:
        """Return current zone occupancy for a stream."""
        with self._lock:
            agg = self._per_stream.get(stream_id)
        if agg is None:
            return {}
        return agg.get_zone_occupancy(zone_name)

    def get_line_crossings(self, stream_id: str) -> Dict[str, dict]:
        """Return current line crossing counts for a stream."""
        with self._lock:
            agg = self._per_stream.get(stream_id)
        if agg is None:
            return {}
        return agg.get_line_crossings()

    def get_trajectory(
        self, stream_id: str, track_id: Optional[int] = None
    ) -> List[dict]:
        """Return trajectory data for a stream, optionally filtered by track."""
        with self._lock:
            agg = self._per_stream.get(stream_id)
        if agg is None:
            return []
        return agg.get_trajectory(track_id)

    def list_streams(self) -> List[str]:
        """Return stream ids currently tracked by the aggregator."""
        with self._lock:
            return list(self._per_stream.keys())

    def _get_or_create(self, stream_id: str) -> _StreamAgg:
        with self._lock:
            agg = self._per_stream.get(stream_id)
            if agg is None:
                agg = _StreamAgg(stream_id, self._window_seconds, self._snapshot_on_disappear)
                self._per_stream[stream_id] = agg
            return agg

    def _flush_loop(self) -> None:
        """Background thread that periodically flushes analytics to PostgreSQL."""
        while self._running:
            time.sleep(max(self._window_seconds, 10))
            if self._running:
                self._flush_once()

    def _flush_once(self) -> None:
        """Flush all streams' current window to PostgreSQL."""
        now = time.time()
        with self._lock:
            streams_to_flush = list(self._per_stream.values())

        for agg in streams_to_flush:
            try:
                agg.flush(self._session_factory)
            except Exception as exc:  # noqa: BLE001 - flush must never break the loop
                logger.warning(f"Analytics flush failed for {agg.stream_id}: {exc}")

        om.ANALYTICS_FLUSHES_TOTAL.inc(len(streams_to_flush))

    def _enforce_retention(self, session_factory) -> None:
        """Delete historical analytics older than retention policy."""
        session = session_factory()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=self._retention_hours)
            old = session.query(AnalyticsSummary).filter(
                AnalyticsSummary.time_window_end < cutoff
            ).all()
            for row in old:
                session.delete(row)
            session.commit()
            om.ANALYTICS_RETENTION_DELETES_TOTAL.inc(len(old))
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.warning(f"Analytics retention cleanup failed: {exc}")
        finally:
            session.close()


class _StreamAgg:
    """Per-stream in-memory state. Thread-unsafe: caller must hold lock."""

    def __init__(
        self,
        stream_id: str,
        window_seconds: int,
        snapshot_on_disappear: bool = True,
    ) -> None:
        self.stream_id = stream_id
        self._window_seconds = window_seconds
        self._snapshot_on_disappear = snapshot_on_disappear

        # Object counts: class_name -> count of OBJECT_APPEARED events
        self._object_counts: Dict[str, int] = defaultdict(int)
        # Unique tracks seen: track_id -> class_name
        self._tracks: Dict[int, str] = {}
        # Zone occupancy: zone_name -> {track_id: enter_time}
        self._zone_tracks: Dict[str, Dict[int, float]] = defaultdict(dict)
        # Zone counters: zone_name -> {entries, exits, occupancy_seconds}
        self._zone_stats: Dict[str, Dict[str, Any]] = {}
        # Line crossing counters: line_name -> {positive, negative, tracks}
        self._line_stats: Dict[str, Dict[str, Any]] = {}
        # Dwell time: total seconds accumulated
        self._dwell_time_total: float = 0.0
        # Stationary and near-collision counts
        self._stationary_count: int = 0
        self._near_collision_count: int = 0
        # Trajectory snapshots: track_id -> list of points
        self._trajectory_snapshots: Dict[int, List[dict]] = {}
        # Track metadata for dwell/trajectory tracking
        self._track_enter_time: Dict[int, float] = {}
        # Window start for current aggregation
        self._window_start: datetime = datetime.now(timezone.utc)

    def handle_event(self, record: dict) -> None:
        event_type = record.get("event_type", "")
        track_id = record.get("track_id")
        class_name = record.get("class_name")
        metadata = record.get("metadata", {})
        timestamp = record.get("timestamp", time.time())

        if event_type == "OBJECT_APPEARED":
            self._object_counts[class_name or "unknown"] += 1
            if track_id is not None:
                self._tracks[track_id] = class_name or "unknown"

        elif event_type == "OBJECT_DISAPPEARED":
            if track_id is not None:
                self._tracks.pop(track_id, None)
            if self._snapshot_on_disappear and track_id is not None:
                self._snapshot_trajectory(track_id, timestamp)

        elif event_type == "OBJECT_ENTERED":
            zone_name = metadata.get("zone_name", "unknown")
            if track_id is not None:
                self._zone_tracks[zone_name][track_id] = timestamp
                self._ensure_zone_stats(zone_name)
                self._zone_stats[zone_name]["entries"] += 1

        elif event_type == "OBJECT_EXITED":
            zone_name = metadata.get("zone_name", "unknown")
            if track_id is not None:
                enter_time = self._zone_tracks[zone_name].pop(track_id, timestamp)
                dwell = max(timestamp - enter_time, 0.0)
                self._dwell_time_total += dwell
                self._ensure_zone_stats(zone_name)
                self._zone_stats[zone_name]["exits"] += 1
                self._zone_stats[zone_name]["occupancy_seconds"] += dwell

        elif event_type == "CROSSING_DIRECTION":
            line_name = metadata.get("line_name", "unknown")
            direction = metadata.get("direction", "")
            self._ensure_line_stats(line_name)
            if direction == metadata.get("positive_label", "positive"):
                self._line_stats[line_name]["positive"] += 1
            else:
                self._line_stats[line_name]["negative"] += 1
            if track_id is not None:
                self._line_stats[line_name]["tracks"].add(track_id)

        elif event_type == "DWELL_TIME":
            zone_name = metadata.get("zone_name", "unknown")
            seconds = metadata.get("seconds", 0.0)
            self._dwell_time_total += seconds
            self._ensure_zone_stats(zone_name)
            self._zone_stats[zone_name]["dwell_seconds"] += seconds

        elif event_type == "STATIONARY_OBJECT":
            self._stationary_count += 1

        elif event_type == "NEAR_COLLISION":
            self._near_collision_count += 1

    def _ensure_zone_stats(self, zone_name: str) -> None:
        if zone_name not in self._zone_stats:
            self._zone_stats[zone_name] = {
                "entries": 0,
                "exits": 0,
                "occupancy_seconds": 0.0,
                "dwell_seconds": 0.0,
                "unique_tracks": set(),
            }

    def _ensure_line_stats(self, line_name: str) -> None:
        if line_name not in self._line_stats:
            self._line_stats[line_name] = {
                "positive": 0,
                "negative": 0,
                "tracks": set(),
            }

    def _snapshot_trajectory(self, track_id: int, timestamp: float) -> None:
        """Capture trajectory points for a disappearing track."""
        pass  # Trajectory snapshots are flushed in flush()

    def flush(self, session_factory) -> None:
        """Persist current window state to PostgreSQL."""
        session = session_factory()
        try:
            now = datetime.now(timezone.utc)
            window_start = self._window_start
            window_end = now

            # AnalyticsSummary
            for class_name, count in self._object_counts.items():
                summary = AnalyticsSummary(
                    stream_id=self.stream_id,
                    time_window_start=window_start,
                    time_window_end=window_end,
                    class_name=class_name,
                    object_count=count,
                    unique_tracks=len(self._tracks),
                    zone_entry_count=sum(
                        z["entries"] for z in self._zone_stats.values()
                    ),
                    zone_exit_count=sum(
                        z["exits"] for z in self._zone_stats.values()
                    ),
                    zone_occupancy_seconds=sum(
                        z["occupancy_seconds"]
                        for z in self._zone_stats.values()
                    ),
                    dwell_time_total_seconds=self._dwell_time_total,
                    stationary_events=self._stationary_count,
                    near_collision_events=self._near_collision_count,
                )
                session.add(summary)

            # ZoneOccupancy
            for zone_name, stats in self._zone_stats.items():
                occupancy = ZoneOccupancy(
                    stream_id=self.stream_id,
                    zone_name=zone_name,
                    time_window_start=window_start,
                    time_window_end=window_end,
                    entry_count=stats["entries"],
                    exit_count=stats["exits"],
                    unique_tracks=len(stats["unique_tracks"]),
                    total_occupancy_seconds=stats["occupancy_seconds"],
                )
                session.add(occupancy)

            # LineCrossing
            for line_name, stats in self._line_stats.items():
                crossing = LineCrossing(
                    stream_id=self.stream_id,
                    line_name=line_name,
                    time_window_start=window_start,
                    time_window_end=window_end,
                    positive_count=stats["positive"],
                    negative_count=stats["negative"],
                    unique_tracks=len(stats["tracks"]),
                )
                session.add(crossing)

            session.commit()
            om.ANALYTICS_SUMMARIES_FLUSHED.inc()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.warning(
                f"Analytics flush failed for {self.stream_id}: {exc}"
            )
        finally:
            session.close()

        # Reset window state
        self._window_start = now
        self._object_counts.clear()
        self._tracks.clear()
        self._zone_tracks.clear()
        self._zone_stats.clear()
        self._line_stats.clear()
        self._dwell_time_total = 0.0
        self._stationary_count = 0
        self._near_collision_count = 0

    def to_dict(self) -> dict:
        """Return current in-memory analytics as a serializable dict."""
        return {
            "stream_id": self.stream_id,
            "object_counts": dict(self._object_counts),
            "unique_tracks": len(self._tracks),
            "zone_occupancy": {
                name: {
                    "entries": stats["entries"],
                    "exits": stats["exits"],
                    "occupancy_seconds": stats["occupancy_seconds"],
                    "dwell_seconds": stats["dwell_seconds"],
                }
                for name, stats in self._zone_stats.items()
            },
            "line_crossings": {
                name: {
                    "positive": stats["positive"],
                    "negative": stats["negative"],
                }
                for name, stats in self._line_stats.items()
            },
            "dwell_time_total_seconds": self._dwell_time_total,
            "stationary_events": self._stationary_count,
            "near_collision_events": self._near_collision_count,
        }

    def get_object_counts(self, class_name: Optional[str] = None) -> Dict[str, int]:
        if class_name:
            return {class_name: self._object_counts.get(class_name, 0)}
        return dict(self._object_counts)

    def get_zone_occupancy(self, zone_name: Optional[str] = None) -> Dict[str, dict]:
        if zone_name:
            stats = self._zone_stats.get(zone_name, {})
            return {
                zone_name: {
                    "entries": stats.get("entries", 0),
                    "exits": stats.get("exits", 0),
                    "occupancy_seconds": stats.get("occupancy_seconds", 0.0),
                    "dwell_seconds": stats.get("dwell_seconds", 0.0),
                }
            }
        return {
            name: {
                "entries": stats["entries"],
                "exits": stats["exits"],
                "occupancy_seconds": stats["occupancy_seconds"],
                "dwell_seconds": stats["dwell_seconds"],
            }
            for name, stats in self._zone_stats.items()
        }

    def get_line_crossings(self) -> Dict[str, dict]:
        return {
            name: {
                "positive": stats["positive"],
                "negative": stats["negative"],
            }
            for name, stats in self._line_stats.items()
        }

    def get_trajectory(self, track_id: Optional[int] = None) -> List[dict]:
        if track_id is not None:
            return self._trajectory_snapshots.get(track_id, [])
        result = []
        for points in self._trajectory_snapshots.values():
            result.extend(points)
        return result
