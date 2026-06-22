"""
Geometry-aware event detectors.

These detectors turn scene geometry (zones and crossing lines, configured on
`EventEngineConfig`) into events. They plug into the same `EventDetector`
interface as the config-free detectors and keep the same bounded-memory
contract: all per-track state is pruned every frame to the live track set.

  - ZoneDetector         -> OBJECT_ENTERED / OBJECT_EXITED / DWELL_TIME
  - LineCrossingDetector -> CROSSING_DIRECTION

Both operate on detection centroids in the same pixel space as the zones/lines.
They consider only *active* tracks (a track briefly lost by the tracker keeps
its zone membership frozen until it is re-associated, so a transient loss does
not fabricate spurious enter/exit events). When a track is finally deleted its
state is dropped in `cleanup()`.

Semantics note: "exit" means a geometric exit *while still tracked*. A track
that vanishes while inside a zone does not emit OBJECT_EXITED — that case is
already signalled by OBJECT_DISAPPEARED from the appearance detector.
"""

from __future__ import annotations

from typing import Optional

from .detectors import EventDetector, FrameContext, _new_event
from .event_types import EventType, Severity
from .geometry import Point


class ZoneDetector(EventDetector):
    """
    Zone membership detector: entry, exit, and dwell.

    Computes each active track's set of containing zones once per frame and
    diffs it against the previous frame to emit OBJECT_ENTERED / OBJECT_EXITED.
    While a track stays inside a zone, a single DWELL_TIME event fires once its
    continuous time inside reaches `config.dwell_seconds` (measured on
    source-frame timestamps, re-armed on exit).
    """

    def __init__(self) -> None:
        # track_id -> set of zone names the track is currently inside
        self._inside: dict[int, frozenset[str]] = {}
        # (track_id, zone_name) -> source timestamp of (continuous) entry
        self._entry_ts: dict[tuple[int, str], float] = {}
        # (track_id, zone_name) pairs that have already emitted a dwell event
        self._dwell_fired: set[tuple[int, str]] = set()

    def update(self, ctx: FrameContext) -> list:
        cfg = ctx.config
        if not cfg.zones:
            return []

        events: list = []

        for obj in ctx.active_objects():
            tid = obj.track_id
            det = obj.current_detection
            centroid = det.center()
            class_name = det.class_name

            current_zones = frozenset(
                zone.name for zone in cfg.zones if zone.contains(centroid)
            )
            prev_zones = self._inside.get(tid, frozenset())

            # --- Entries ---
            for zone_name in sorted(current_zones - prev_zones):
                self._entry_ts[(tid, zone_name)] = ctx.timestamp
                self._dwell_fired.discard((tid, zone_name))
                events.append(
                    _new_event(
                        ctx,
                        event_type=EventType.OBJECT_ENTERED,
                        severity=Severity.LOW,
                        track_id=tid,
                        class_name=class_name,
                        message=f"{class_name or 'object'} entered zone {zone_name!r}",
                        metadata={"zone": zone_name},
                    )
                )

            # --- Exits ---
            for zone_name in sorted(prev_zones - current_zones):
                key = (tid, zone_name)
                entry = self._entry_ts.pop(key, None)
                self._dwell_fired.discard(key)
                dwell = (ctx.timestamp - entry) if entry is not None else 0.0
                events.append(
                    _new_event(
                        ctx,
                        event_type=EventType.OBJECT_EXITED,
                        severity=Severity.LOW,
                        track_id=tid,
                        class_name=class_name,
                        message=f"{class_name or 'object'} exited zone {zone_name!r}",
                        metadata={
                            "zone": zone_name,
                            "dwell_seconds": round(max(dwell, 0.0), 3),
                        },
                    )
                )

            # --- Dwell (for zones the track remains inside) ---
            for zone_name in sorted(current_zones):
                key = (tid, zone_name)
                entry = self._entry_ts.get(key)
                if entry is None:
                    # Defensive: a track present in a zone should always have an
                    # entry timestamp set above; seed it if somehow missing.
                    self._entry_ts[key] = ctx.timestamp
                    continue
                if key in self._dwell_fired:
                    continue
                elapsed = ctx.timestamp - entry
                if elapsed >= cfg.dwell_seconds:
                    self._dwell_fired.add(key)
                    events.append(
                        _new_event(
                            ctx,
                            event_type=EventType.DWELL_TIME,
                            severity=Severity.HIGH,
                            track_id=tid,
                            class_name=class_name,
                            message=(
                                f"{class_name or 'object'} dwelling in zone "
                                f"{zone_name!r} for {elapsed:.1f}s"
                            ),
                            metadata={
                                "zone": zone_name,
                                "dwell_seconds": round(elapsed, 3),
                                "threshold_seconds": cfg.dwell_seconds,
                            },
                        )
                    )

            self._inside[tid] = current_zones

        return events

    def cleanup(self, active_ids: set) -> None:
        self._inside = {
            tid: zones for tid, zones in self._inside.items() if tid in active_ids
        }
        self._entry_ts = {
            key: ts for key, ts in self._entry_ts.items() if key[0] in active_ids
        }
        self._dwell_fired = {
            key for key in self._dwell_fired if key[0] in active_ids
        }

    def reset(self) -> None:
        self._inside.clear()
        self._entry_ts.clear()
        self._dwell_fired.clear()


class LineCrossingDetector(EventDetector):
    """
    Emits CROSSING_DIRECTION when a track's centroid path crosses a configured
    line segment, reporting the named direction of the crossing.

    Holds the last centroid per track so the motion since the previous frame can
    be tested against every line. A single motion that crosses several lines
    emits one event per line.
    """

    def __init__(self) -> None:
        # track_id -> last observed centroid
        self._prev: dict[int, Point] = {}

    def update(self, ctx: FrameContext) -> list:
        cfg = ctx.config
        if not cfg.lines:
            # Still nothing to do, but keep _prev empty so it cannot grow.
            self._prev.clear()
            return []

        events: list = []

        for obj in ctx.active_objects():
            tid = obj.track_id
            det = obj.current_detection
            cur = det.center()
            prev = self._prev.get(tid)

            if prev is not None:
                for line in cfg.lines:
                    direction = line.direction_of(prev, cur)
                    if direction is None:
                        continue
                    events.append(
                        _new_event(
                            ctx,
                            event_type=EventType.CROSSING_DIRECTION,
                            severity=Severity.MEDIUM,
                            track_id=tid,
                            class_name=det.class_name,
                            message=(
                                f"{det.class_name or 'object'} crossed line "
                                f"{line.name!r} ({direction})"
                            ),
                            metadata={
                                "line": line.name,
                                "direction": direction,
                                "from": [round(prev[0], 2), round(prev[1], 2)],
                                "to": [round(cur[0], 2), round(cur[1], 2)],
                            },
                        )
                    )

            self._prev[tid] = cur

        return events

    def cleanup(self, active_ids: set) -> None:
        self._prev = {
            tid: pt for tid, pt in self._prev.items() if tid in active_ids
        }

    def reset(self) -> None:
        self._prev.clear()
