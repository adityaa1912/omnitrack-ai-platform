"""REST API for analytics queries.

Provides endpoints for current and historical analytics, zone occupancy,
line crossings, and trajectory data. Protected by the existing RBAC layer
(viewer reads, operator/admin manages config).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth.dependencies import CurrentUser, get_db, get_current_user, require_role
from ..cache.json_cache import JsonCache
from ..models import Base
from .aggregator import AnalyticsAggregator
from .cache import AnalyticsCache
from backend.observability.metrics import ANALYTICS_QUERY_LATENCY
from .models import AnalyticsSummary, LineCrossing, TrajectorySnapshot, ZoneOccupancy

router = APIRouter(prefix="/analytics", tags=["analytics"])

_manager: Optional[AnalyticsAggregator] = None
_db_cache: Optional[JsonCache] = None


def set_manager(manager: AnalyticsAggregator, cache: Optional[JsonCache] = None) -> None:
    global _manager, _db_cache
    _manager = manager
    _db_cache = cache


def _get_cache() -> AnalyticsCache:
    return AnalyticsCache(_db_cache)


@router.get("/{stream_id}/current")
async def get_current_analytics(
    stream_id: str,
    user: CurrentUser = Depends(require_role("viewer")),
    cache: AnalyticsCache = Depends(lambda: _get_cache()),
):
    """Return current in-memory analytics for a stream."""
    start = time.perf_counter()
    try:
        cache_key = f"analytics:{stream_id}:current"
        cached = cache.get(cache_key)
        if cached is not None:
            ANALYTICS_CACHE_HITS_TOTAL.inc()
            return cached

        if _manager is None:
            raise HTTPException(status_code=503, detail="Analytics engine not initialized")

        data = _manager.get_current(stream_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"No analytics data for stream {stream_id}")

        cache.set(data, cache_key)
        ANALYTICS_CACHE_MISSES_TOTAL.inc()
        return data
    finally:
        elapsed = time.perf_counter() - start
        ANALYTICS_QUERY_LATENCY.observe(max(elapsed, 0.0))


@router.get("/{stream_id}/object-counts")
async def get_object_counts(
    stream_id: str,
    class_name: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(require_role("viewer")),
    cache: AnalyticsCache = Depends(lambda: _get_cache()),
):
    """Return current per-class object counts for a stream."""
    start = time.perf_counter()
    try:
        cache_key = f"analytics:{stream_id}:objects{f':{class_name}' if class_name else ''}"
        cached = cache.get(cache_key)
        if cached is not None:
            ANALYTICS_CACHE_HITS_TOTAL.inc()
            return cached

        if _manager is None:
            raise HTTPException(status_code=503, detail="Analytics engine not initialized")

        counts = _manager.get_object_counts(stream_id, class_name)
        cache.set(counts, cache_key)
        ANALYTICS_CACHE_MISSES_TOTAL.inc()
        return {"stream_id": stream_id, "counts": counts}
    finally:
        elapsed = time.perf_counter() - start
        ANALYTICS_QUERY_LATENCY.observe(max(elapsed, 0.0))


@router.get("/{stream_id}/zone-occupancy")
async def get_zone_occupancy(
    stream_id: str,
    zone_name: Optional[str] = Query(default=None),
    user: CurrentUser = Depends(require_role("viewer")),
    cache: AnalyticsCache = Depends(lambda: _get_cache()),
):
    """Return current zone occupancy for a stream."""
    start = time.perf_counter()
    try:
        cache_key = f"analytics:{stream_id}:zones{f':{zone_name}' if zone_name else ''}"
        cached = cache.get(cache_key)
        if cached is not None:
            ANALYTICS_CACHE_HITS_TOTAL.inc()
            return cached

        if _manager is None:
            raise HTTPException(status_code=503, detail="Analytics engine not initialized")

        data = _manager.get_zone_occupancy(stream_id, zone_name)
        result = {"stream_id": stream_id, "zones": data}
        cache.set(result, cache_key)
        ANALYTICS_CACHE_MISSES_TOTAL.inc()
        return result
    finally:
        elapsed = time.perf_counter() - start
        ANALYTICS_QUERY_LATENCY.observe(max(elapsed, 0.0))


@router.get("/{stream_id}/line-crossings")
async def get_line_crossings(
    stream_id: str,
    user: CurrentUser = Depends(require_role("viewer")),
    cache: AnalyticsCache = Depends(lambda: _get_cache()),
):
    """Return current line crossing counts for a stream."""
    start = time.perf_counter()
    try:
        cache_key = f"analytics:{stream_id}:crossings"
        cached = cache.get(cache_key)
        if cached is not None:
            ANALYTICS_CACHE_HITS_TOTAL.inc()
            return cached

        if _manager is None:
            raise HTTPException(status_code=503, detail="Analytics engine not initialized")

        data = _manager.get_line_crossings(stream_id)
        result = {"stream_id": stream_id, "crossings": data}
        cache.set(result, cache_key)
        ANALYTICS_CACHE_MISSES_TOTAL.inc()
        return result
    finally:
        elapsed = time.perf_counter() - start
        ANALYTICS_QUERY_LATENCY.observe(max(elapsed, 0.0))


@router.get("/{stream_id}/trajectory")
async def get_trajectory(
    stream_id: str,
    track_id: Optional[int] = Query(default=None),
    user: CurrentUser = Depends(require_role("viewer")),
):
    """Return trajectory data for a stream, optionally filtered by track."""
    start = time.perf_counter()
    try:
        if _manager is None:
            raise HTTPException(status_code=503, detail="Analytics engine not initialized")

        data = _manager.get_trajectory(stream_id, track_id)
        return {"stream_id": stream_id, "track_id": track_id, "points": data}
    finally:
        elapsed = time.perf_counter() - start
        ANALYTICS_QUERY_LATENCY.observe(max(elapsed, 0.0))


@router.get("/{stream_id}/history")
async def get_analytics_history(
    stream_id: str,
    hours: int = Query(default=24, ge=1, le=720, description="Number of hours of history to return"),
    class_name: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    user: CurrentUser = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    """Return historical analytics summaries for a stream from PostgreSQL."""
    start = time.perf_counter()
    try:
        if _manager is None:
            raise HTTPException(status_code=503, detail="Analytics engine not initialized")

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = db.query(AnalyticsSummary).filter(
            AnalyticsSummary.stream_id == stream_id,
            AnalyticsSummary.time_window_start >= cutoff,
        )
        if class_name:
            query = query.filter(AnalyticsSummary.class_name == class_name)
        query = query.order_by(AnalyticsSummary.time_window_start.desc()).limit(limit)
        rows = query.all()

        return {
            "stream_id": stream_id,
            "hours": hours,
            "class_name": class_name,
            "summaries": [
                {
                    "id": row.id,
                    "time_window_start": row.time_window_start.isoformat(),
                    "time_window_end": row.time_window_end.isoformat(),
                    "class_name": row.class_name,
                    "object_count": row.object_count,
                    "unique_tracks": row.unique_tracks,
                    "zone_entry_count": row.zone_entry_count,
                    "zone_exit_count": row.zone_exit_count,
                    "zone_occupancy_seconds": row.zone_occupancy_seconds,
                    "line_crossing_positive": row.line_crossing_positive_count,
                    "line_crossing_negative": row.line_crossing_negative_count,
                    "dwell_time_total_seconds": row.dwell_time_total_seconds,
                    "stationary_events": row.stationary_events,
                    "near_collision_events": row.near_collision_events,
                }
                for row in rows
            ],
        }
    finally:
        elapsed = time.perf_counter() - start
        ANALYTICS_QUERY_LATENCY.observe(max(elapsed, 0.0))


@router.get("/{stream_id}/zone-history")
async def get_zone_history(
    stream_id: str,
    zone_name: str,
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=100, ge=1, le=1000),
    user: CurrentUser = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    """Return historical zone occupancy for a stream from PostgreSQL."""
    start = time.perf_counter()
    try:
        if _manager is None:
            raise HTTPException(status_code=503, detail="Analytics engine not initialized")

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = (
            db.query(ZoneOccupancy)
            .filter(
                ZoneOccupancy.stream_id == stream_id,
                ZoneOccupancy.zone_name == zone_name,
                ZoneOccupancy.time_window_start >= cutoff,
            )
            .order_by(ZoneOccupancy.time_window_start.desc())
            .limit(limit)
            .all()
        )

        return {
            "stream_id": stream_id,
            "zone_name": zone_name,
            "hours": hours,
            "records": [
                {
                    "id": row.id,
                    "time_window_start": row.time_window_start.isoformat(),
                    "time_window_end": row.time_window_end.isoformat(),
                    "entry_count": row.entry_count,
                    "exit_count": row.exit_count,
                    "unique_tracks": row.unique_tracks,
                    "total_occupancy_seconds": row.total_occupancy_seconds,
                    "max_concurrent_tracks": row.max_concurrent_tracks,
                }
                for row in rows
            ],
        }
    finally:
        elapsed = time.perf_counter() - start
        ANALYTICS_QUERY_LATENCY.observe(max(elapsed, 0.0))


@router.get("/{stream_id}/line-history")
async def get_line_history(
    stream_id: str,
    line_name: str,
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=100, ge=1, le=1000),
    user: CurrentUser = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    """Return historical line crossing counts for a stream from PostgreSQL."""
    start = time.perf_counter()
    try:
        if _manager is None:
            raise HTTPException(status_code=503, detail="Analytics engine not initialized")

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = (
            db.query(LineCrossing)
            .filter(
                LineCrossing.stream_id == stream_id,
                LineCrossing.line_name == line_name,
                LineCrossing.time_window_start >= cutoff,
            )
            .order_by(LineCrossing.time_window_start.desc())
            .limit(limit)
            .all()
        )

        return {
            "stream_id": stream_id,
            "line_name": line_name,
            "hours": hours,
            "records": [
                {
                    "id": row.id,
                    "time_window_start": row.time_window_start.isoformat(),
                    "time_window_end": row.time_window_end.isoformat(),
                    "positive_count": row.positive_count,
                    "negative_count": row.negative_count,
                    "unique_tracks": row.unique_tracks,
                }
                for row in rows
            ],
        }
    finally:
        elapsed = time.perf_counter() - start
        ANALYTICS_QUERY_LATENCY.observe(max(elapsed, 0.0))


@router.get("/{stream_id}/trajectory-history")
async def get_trajectory_history(
    stream_id: str,
    track_id: Optional[int] = Query(default=None),
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=100, ge=1, le=1000),
    user: CurrentUser = Depends(require_role("viewer")),
    db: Session = Depends(get_db),
):
    """Return historical trajectory snapshots for a stream from PostgreSQL."""
    start = time.perf_counter()
    try:
        if _manager is None:
            raise HTTPException(status_code=503, detail="Analytics engine not initialized")

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = db.query(TrajectorySnapshot).filter(
            TrajectorySnapshot.stream_id == stream_id,
            TrajectorySnapshot.time_window_start >= cutoff,
        )
        if track_id is not None:
            query = query.filter(TrajectorySnapshot.track_id == track_id)
        query = query.order_by(TrajectorySnapshot.time_window_start.desc()).limit(limit)
        rows = query.all()

        return {
            "stream_id": stream_id,
            "track_id": track_id,
            "hours": hours,
            "snapshots": [
                {
                    "id": row.id,
                    "track_id": row.track_id,
                    "class_name": row.class_name,
                    "time_window_start": row.time_window_start.isoformat(),
                    "time_window_end": row.time_window_end.isoformat(),
                    "first_seen_frame": row.first_seen_frame,
                    "last_seen_frame": row.last_seen_frame,
                    "points": row.points,
                    "total_distance_meters": row.total_distance_meters,
                    "avg_speed_mps": row.avg_speed_mps,
                }
                for row in rows
            ],
        }
    finally:
        elapsed = time.perf_counter() - start
        ANALYTICS_QUERY_LATENCY.observe(max(elapsed, 0.0))


@router.get("/streams")
async def list_analytics_streams(
    user: CurrentUser = Depends(require_role("viewer")),
):
    """List all streams currently tracked by the analytics aggregator."""
    if _manager is None:
        raise HTTPException(status_code=503, detail="Analytics engine not initialized")
    return {"streams": _manager.list_streams()}


@router.post("/{stream_id}/flush")
async def flush_analytics(
    stream_id: str,
    user: CurrentUser = Depends(require_role("operator")),
):
    """Force-flush analytics for a stream to PostgreSQL. Operator only."""
    if _manager is None:
        raise HTTPException(status_code=503, detail="Analytics engine not initialized")

    with _manager._lock:
        agg = _manager._per_stream.get(stream_id)
    if agg is None:
        raise HTTPException(status_code=404, detail=f"No analytics data for stream {stream_id}")

    session = _manager._session_factory()
    try:
        agg.flush(session)
        return {"status": "flushed", "stream_id": stream_id}
    finally:
        session.close()
