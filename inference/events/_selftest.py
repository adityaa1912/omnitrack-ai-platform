"""
Dependency-free self-test for the event engine.

Run with:  python -m inference.events._selftest

Drives synthetic detections through the *real* CentroidTracker and asserts the
events the engine derives. Kept as a runnable module (not pytest) because this
project has no test harness configured; it exits non-zero on failure so it can
gate CI later.
"""

from __future__ import annotations

from collections import Counter

from ..tracker import CentroidTracker
from ..tracking_config import TrackingConfig
from ..types import Detection
from .config import EventEngineConfig
from .engine import EventEngine
from .event_types import EventType, Severity


def _box(cx: float, cy: float, half: float = 10.0) -> tuple[float, float, float, float]:
    return (cx - half, cy - half, cx + half, cy + half)


def _det(cx: float, cy: float, class_name: str) -> Detection:
    x1, y1, x2, y2 = _box(cx, cy)
    return Detection(
        x1=x1, y1=y1, x2=x2, y2=y2,
        class_id=0, confidence=0.9, class_name=class_name,
    )


def _build_timeline() -> list[list[Detection]]:
    """
    A (person) is stationary at (100,100) for frames 0..19, then leaves.
    B (car) appears at frame 5, approaches A (near collision), then leaves
    after frame 12. Tail frames are empty so both tracks are deleted.
    """
    timeline: list[list[Detection]] = []
    for f in range(0, 26):
        dets: list[Detection] = []
        if 0 <= f <= 19:
            dets.append(_det(100, 100, "person"))  # A: stationary
        if 5 <= f <= 12:
            bx = 300 - (f - 5) * 25  # 300 -> 125 as it approaches A
            dets.append(_det(bx, 100, "car"))       # B: moving
        timeline.append(dets)
    return timeline


def run() -> int:
    tracker = CentroidTracker(
        TrackingConfig(centroid_distance_threshold=50.0, max_age=3)
    )
    engine = EventEngine(
        stream_id="selftest",
        config=EventEngineConfig(
            stationary_min_frames=5,
            stationary_max_spread_px=3.0,
            stationary_clear_spread_px=6.0,
            near_collision_distance_px=40.0,
            near_collision_clear_distance_px=70.0,
        ),
    )

    all_events = []
    for frame_id, dets in enumerate(_build_timeline()):
        result = tracker.update(dets, frame_id, frame_id / 30.0)
        all_events.extend(engine.process(result))

    counts = Counter(e.event_type for e in all_events)
    print("Derived events:")
    for e in all_events:
        print(f"  f{e.frame_id:>2} {e.severity.value:<8} {e.event_type.value:<20} {e.message}")
    print("Counts:", {k.value: v for k, v in counts.items()})

    checks = [
        ("two objects appeared", counts[EventType.OBJECT_APPEARED] == 2),
        ("two objects disappeared", counts[EventType.OBJECT_DISAPPEARED] == 2),
        ("one stationary event", counts[EventType.STATIONARY_OBJECT] == 1),
        ("one near-collision event", counts[EventType.NEAR_COLLISION] == 1),
        (
            "near-collision is CRITICAL",
            all(
                e.severity is Severity.CRITICAL
                for e in all_events
                if e.event_type is EventType.NEAR_COLLISION
            ),
        ),
        (
            "near-collision carries both track ids",
            all(
                len(e.metadata.get("track_ids", [])) == 2
                for e in all_events
                if e.event_type is EventType.NEAR_COLLISION
            ),
        ),
        ("engine state pruned to empty", len(engine._known_ids) == 0),
    ]

    ok = True
    print("\nChecks:")
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed

    # Idempotent reset must clear everything.
    engine.reset()
    if engine._meta_by_id or engine._known_ids:
        print("  [FAIL] reset() left residual state")
        ok = False
    else:
        print("  [PASS] reset() clears state")

    print("\n" + ("ALL CHECKS PASSED" if ok else "SELF-TEST FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(run())
