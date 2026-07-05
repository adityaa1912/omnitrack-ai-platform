"""
Event engine.

One `EventEngine` per stream. It consumes the `TrackingResult` produced each
frame by the tracker and returns the events derived from it, delegating the
actual rules to a list of `EventDetector`s.

Design properties:
  - Derives strictly from tracking output; never touches frames or rendering.
  - Bounded memory: per-track metadata and detector state are pruned every
    frame to the set of tracks the tracker still holds.
  - Sync, no I/O. `process()` normally runs on a single (inference) thread, but
    `reconfigure()` may be called concurrently from another thread to change the
    scene geometry live; a lock makes the two mutually exclusive, so a frame is
    never derived against a half-swapped configuration.
"""

from __future__ import annotations

import threading

from typing import Optional

from ..tracking_types import TrackedObject, TrackingResult
from .config import EventEngineConfig
from .detectors import (
    AppearanceDetector,
    EventDetector,
    FrameContext,
    NearCollisionDetector,
    StationaryDetector,
    TrackMeta,
)
from .event_types import Event
from .geometry_detectors import LineCrossingDetector, ZoneDetector


def default_detectors() -> list[EventDetector]:
    """
    All built-in detectors.

    The geometry detectors (zone/line) are inert until zones/lines are
    configured on `EventEngineConfig`, so this default set is safe for any
    stream — an unconfigured stream produces only the config-free events.
    """
    return [
        AppearanceDetector(),
        StationaryDetector(),
        NearCollisionDetector(),
        ZoneDetector(),
        LineCrossingDetector(),
    ]


class EventEngine:
    """Stateful per-stream event derivation over tracking results."""

    def __init__(
        self,
        stream_id: str,
        config: Optional[EventEngineConfig] = None,
        detectors: Optional[list[EventDetector]] = None,
    ) -> None:
        self.stream_id = stream_id
        self.config = config or EventEngineConfig()
        self._detectors = detectors if detectors is not None else default_detectors()

        # Bounded by the number of live tracks; pruned each frame.
        self._known_ids: set[int] = set()
        self._meta_by_id: dict[int, TrackMeta] = {}

        # Serializes process()/reconfigure()/reset() so a live geometry change
        # never interleaves with a frame's derivation. Uncontended in the common
        # single-thread case (negligible cost next to detection/tracking).
        self._lock = threading.Lock()

    def process(self, result: TrackingResult) -> list[Event]:
        """
        Derive events for one frame.

        Returns events in a stable order (appearance first, then per-detector).
        Returns an empty list when disabled, so callers need no special-casing.
        Holds the engine lock so a concurrent `reconfigure()` cannot swap the
        scene geometry mid-frame.
        """
        with self._lock:
            return self._process_locked(result)

    def _process_locked(self, result: TrackingResult) -> list[Event]:
        if not self.config.enabled:
            return []

        objects_by_id = {obj.track_id: obj for obj in result.tracked_objects}
        current_ids = set(objects_by_id.keys())

        appeared_ids = current_ids - self._known_ids
        disappeared_ids = self._known_ids - current_ids

        # Refresh meta for present tracks BEFORE building the context, so
        # detectors (and the disappeared mapping) see the latest known values.
        self._refresh_meta(objects_by_id, result.frame_id)

        ctx = FrameContext(
            stream_id=self.stream_id,
            frame_id=result.frame_id,
            timestamp=self._frame_timestamp(result, objects_by_id),
            config=self.config,
            objects_by_id=objects_by_id,
            current_ids=current_ids,
            appeared_ids=appeared_ids,
            disappeared_ids=disappeared_ids,
            meta_by_id=self._meta_by_id,
        )

        events: list[Event] = []
        for detector in self._detectors:
            events.extend(detector.update(ctx))

        # Prune all state down to the live track set.
        for detector in self._detectors:
            detector.cleanup(current_ids)
        for track_id in disappeared_ids:
            self._meta_by_id.pop(track_id, None)
        self._known_ids = current_ids

        return events

    def reset(self) -> None:
        """Clear engine and detector state (e.g. on stream restart)."""
        with self._lock:
            self._known_ids.clear()
            self._meta_by_id.clear()
            for detector in self._detectors:
                detector.reset()

    def reconfigure(self, config: EventEngineConfig) -> None:
        """
        Swap the scene geometry (zones/lines/dwell) on a running engine.

        Rebuilds ONLY the geometry detectors so their per-track state matches the
        new regions; the appearance/stationary/near-collision detectors and their
        hysteresis are preserved, so changing regions never fires spurious
        appeared/disappeared events. Track identity/meta is likewise preserved.
        Thread-safe against `process()` via the engine lock, so it can be called
        live from another thread. Only the geometry detectors are replaced, so
        this assumes the standard detector set (as built by `default_detectors`).
        """
        with self._lock:
            self.config = config
            self._detectors = [
                detector
                for detector in self._detectors
                if not isinstance(detector, (ZoneDetector, LineCrossingDetector))
            ] + [ZoneDetector(), LineCrossingDetector()]

    # -- internals ----------------------------------------------------------

    def _refresh_meta(
        self, objects_by_id: dict[int, TrackedObject], frame_id: int
    ) -> None:
        for track_id, obj in objects_by_id.items():
            det = obj.current_detection
            centroid = det.center() if det is not None else (0.0, 0.0)
            class_name = det.class_name if det is not None else None
            ts = self._object_timestamp(obj)

            existing = self._meta_by_id.get(track_id)
            if existing is None:
                self._meta_by_id[track_id] = TrackMeta(
                    track_id=track_id,
                    class_name=class_name,
                    first_frame=obj.first_seen_frame,
                    first_timestamp=ts,
                    last_frame=frame_id,
                    last_timestamp=ts,
                    last_centroid=centroid,
                )
            else:
                self._meta_by_id[track_id] = TrackMeta(
                    track_id=track_id,
                    class_name=class_name or existing.class_name,
                    first_frame=existing.first_frame,
                    first_timestamp=existing.first_timestamp,
                    last_frame=frame_id,
                    last_timestamp=ts,
                    last_centroid=centroid,
                )

    @staticmethod
    def _object_timestamp(obj: TrackedObject) -> float:
        """Latest trajectory timestamp for a track, or 0.0 if none yet."""
        points = obj.trajectory_points
        return points[-1].timestamp if len(points) > 0 else 0.0

    def _frame_timestamp(
        self, result: TrackingResult, objects_by_id: dict[int, TrackedObject]
    ) -> float:
        """
        Frame timestamp inferred from the freshest track. TrackingResult has no
        timestamp field, but trajectory points carry the source frame time, so
        we take the maximum across current tracks (0.0 when the frame is empty).
        """
        latest = 0.0
        for obj in objects_by_id.values():
            ts = self._object_timestamp(obj)
            if ts > latest:
                latest = ts
        return latest
