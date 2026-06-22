"""
Event detectors.

A detector inspects the per-frame `FrameContext` and returns zero or more
`Event`s. Each detector owns its own state, which MUST stay bounded by the
number of live tracks (or track-pairs): `cleanup()` is called every frame with
the set of currently-known track ids so detectors can drop state for tracks
that no longer exist. This is what keeps the engine bounded-memory regardless
of how long a stream runs.

This module holds the detectors that need no scene geometry:
  - AppearanceDetector  -> OBJECT_APPEARED / OBJECT_DISAPPEARED
  - StationaryDetector  -> STATIONARY_OBJECT
  - NearCollisionDetector -> NEAR_COLLISION

It also defines the shared detector interface (`EventDetector`, `FrameContext`,
`TrackMeta`). The geometry-dependent detectors (entered/exited/crossing/dwell)
live in `geometry_detectors.py` and plug into the same interface.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from ..tracking_types import TrackedObject
from .config import EventEngineConfig
from .event_types import Event, EventType, Severity


@dataclass(frozen=True)
class TrackMeta:
    """Last-known summary for a track, retained so DISAPPEARED events can be
    described after the track is gone from the current frame."""

    track_id: int
    class_name: Optional[str]
    first_frame: int
    first_timestamp: float
    last_frame: int
    last_timestamp: float
    last_centroid: tuple[float, float]


@dataclass
class FrameContext:
    """Everything a detector needs for one frame, assembled once by the engine."""

    stream_id: str
    frame_id: int
    timestamp: float
    config: EventEngineConfig

    # All tracks currently held by the tracker (active and briefly-lost).
    objects_by_id: dict[int, TrackedObject]
    current_ids: set[int]

    # Set diffs computed by the engine against the previous frame.
    appeared_ids: set[int]
    disappeared_ids: set[int]

    # Last-known meta, including entries for ids that just disappeared.
    meta_by_id: dict[int, TrackMeta]

    def active_objects(self) -> list[TrackedObject]:
        """Tracks with a usable current detection (not lost this frame)."""
        return [
            obj
            for obj in self.objects_by_id.values()
            if not obj.is_lost and obj.current_detection is not None
        ]


def _new_event(ctx: FrameContext, **kwargs) -> Event:
    """Build an Event stamped with the frame's stream/frame/timestamp."""
    return Event(
        stream_id=ctx.stream_id,
        frame_id=ctx.frame_id,
        timestamp=ctx.timestamp,
        **kwargs,
    )


class EventDetector(ABC):
    """Base class. Subclasses keep only bounded, track-scoped state."""

    @abstractmethod
    def update(self, ctx: FrameContext) -> list[Event]:
        """Inspect the frame and return any events raised this frame."""

    def cleanup(self, active_ids: set[int]) -> None:
        """Drop state for tracks no longer present. Default: nothing to drop."""

    def reset(self) -> None:
        """Clear all internal state."""


class AppearanceDetector(EventDetector):
    """
    Emits OBJECT_APPEARED when a new track id is first seen and
    OBJECT_DISAPPEARED when a track id is dropped by the tracker.

    The id-set diffs are computed centrally by the engine; this detector only
    maps them to events. Using the tracker's create/delete lifecycle (rather
    than the transient lost/found flag) means a track that momentarily drops out
    and is re-associated does not produce a flapping appear/disappear pair.
    """

    def update(self, ctx: FrameContext) -> list[Event]:
        events: list[Event] = []

        for track_id in sorted(ctx.appeared_ids):
            obj = ctx.objects_by_id.get(track_id)
            det = obj.current_detection if obj is not None else None
            class_name = det.class_name if det is not None else None
            events.append(
                _new_event(
                    ctx,
                    event_type=EventType.OBJECT_APPEARED,
                    severity=Severity.INFO,
                    track_id=track_id,
                    class_name=class_name,
                    message=f"{class_name or 'object'} appeared (track {track_id})",
                )
            )

        for track_id in sorted(ctx.disappeared_ids):
            meta = ctx.meta_by_id.get(track_id)
            class_name = meta.class_name if meta is not None else None
            duration = (
                meta.last_timestamp - meta.first_timestamp if meta is not None else 0.0
            )
            events.append(
                _new_event(
                    ctx,
                    event_type=EventType.OBJECT_DISAPPEARED,
                    severity=Severity.INFO,
                    track_id=track_id,
                    class_name=class_name,
                    message=f"{class_name or 'object'} disappeared (track {track_id})",
                    metadata={"visible_seconds": round(max(duration, 0.0), 3)},
                )
            )

        return events


class StationaryDetector(EventDetector):
    """
    Emits STATIONARY_OBJECT once a track's recent trajectory stays within a
    small radius for long enough. Latches per track so it fires a single event,
    and re-arms only after the track clearly moves again (hysteresis).
    """

    def __init__(self) -> None:
        # track_id -> currently latched as stationary
        self._latched: set[int] = set()

    def update(self, ctx: FrameContext) -> list[Event]:
        events: list[Event] = []
        cfg = ctx.config

        for obj in ctx.active_objects():
            spread = _trajectory_spread(obj, cfg.stationary_min_frames)
            if spread is None:
                continue

            if obj.track_id in self._latched:
                # Re-arm once it moves beyond the clear threshold.
                if spread > cfg.stationary_clear_spread_px:
                    self._latched.discard(obj.track_id)
                continue

            if spread <= cfg.stationary_max_spread_px:
                self._latched.add(obj.track_id)
                det = obj.current_detection
                events.append(
                    _new_event(
                        ctx,
                        event_type=EventType.STATIONARY_OBJECT,
                        severity=Severity.MEDIUM,
                        track_id=obj.track_id,
                        class_name=det.class_name if det is not None else None,
                        message=(
                            f"{det.class_name if det else 'object'} stationary "
                            f"(track {obj.track_id})"
                        ),
                        metadata={
                            "spread_px": round(spread, 2),
                            "window_frames": cfg.stationary_min_frames,
                        },
                    )
                )

        return events

    def cleanup(self, active_ids: set[int]) -> None:
        self._latched.intersection_update(active_ids)

    def reset(self) -> None:
        self._latched.clear()


class NearCollisionDetector(EventDetector):
    """
    Emits NEAR_COLLISION when two active tracks' centroids come within the
    configured distance. Latches per unordered pair so a single CRITICAL event
    is raised, re-arming only after the pair separates (hysteresis).
    """

    def __init__(self) -> None:
        # frozenset({id_a, id_b}) -> currently latched as near-collision
        self._latched: set[frozenset[int]] = set()

    def update(self, ctx: FrameContext) -> list[Event]:
        events: list[Event] = []
        cfg = ctx.config

        objects = ctx.active_objects()
        for i in range(len(objects)):
            for j in range(i + 1, len(objects)):
                a, b = objects[i], objects[j]
                pair = frozenset((a.track_id, b.track_id))
                dist = _centroid_distance(a, b)
                if dist is None:
                    continue

                if pair in self._latched:
                    if dist > cfg.near_collision_clear_distance_px:
                        self._latched.discard(pair)
                    continue

                if dist <= cfg.near_collision_distance_px:
                    self._latched.add(pair)
                    det_a, det_b = a.current_detection, b.current_detection
                    events.append(
                        _new_event(
                            ctx,
                            event_type=EventType.NEAR_COLLISION,
                            severity=Severity.CRITICAL,
                            track_id=a.track_id,
                            class_name=det_a.class_name if det_a else None,
                            message=(
                                f"near collision between tracks "
                                f"{a.track_id} and {b.track_id}"
                            ),
                            metadata={
                                "track_ids": sorted((a.track_id, b.track_id)),
                                "partner_track_id": b.track_id,
                                "distance_px": round(dist, 2),
                                "partner_class_name": (
                                    det_b.class_name if det_b else None
                                ),
                            },
                        )
                    )

        return events

    def cleanup(self, active_ids: set[int]) -> None:
        self._latched = {
            pair for pair in self._latched if pair <= active_ids
        }

    def reset(self) -> None:
        self._latched.clear()


# ---------------------------------------------------------------------------
# Geometry helpers (pure functions)
# ---------------------------------------------------------------------------


def _centroid(obj: TrackedObject) -> Optional[tuple[float, float]]:
    det = obj.current_detection
    if det is None:
        return None
    return det.center()


def _centroid_distance(a: TrackedObject, b: TrackedObject) -> Optional[float]:
    ca, cb = _centroid(a), _centroid(b)
    if ca is None or cb is None:
        return None
    return math.hypot(ca[0] - cb[0], ca[1] - cb[1])


def _trajectory_spread(obj: TrackedObject, window: int) -> Optional[float]:
    """
    Spread of the last `window` trajectory points: the max distance of any
    point from their mean. Returns None until at least `window` points exist,
    so a freshly-seen object is never reported stationary prematurely.
    """
    points = obj.trajectory_points
    if len(points) < window:
        return None

    recent = list(points)[-window:]
    mean_x = sum(p.x for p in recent) / len(recent)
    mean_y = sum(p.y for p in recent) / len(recent)
    return max(math.hypot(p.x - mean_x, p.y - mean_y) for p in recent)
