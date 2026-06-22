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
from .regions import CrossingLine, Zone


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


def _print_events(title: str, events: list) -> None:
    print(title)
    for e in events:
        print(
            f"  f{e.frame_id:>2} {e.severity.value:<8} "
            f"{e.event_type.value:<20} {e.message}"
        )


def _report(checks: list[tuple[str, bool]]) -> bool:
    ok = True
    print("Checks:")
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    return ok


def run_lifecycle() -> bool:
    """Scenario 1: appeared / disappeared / stationary / near-collision."""
    tracker = CentroidTracker(
        TrackingConfig(centroid_distance_threshold=50.0, max_age=3)
    )
    engine = EventEngine(
        stream_id="selftest-lifecycle",
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
    _print_events("Derived events:", all_events)
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
        (
            "no geometry events without zones/lines",
            counts[EventType.OBJECT_ENTERED] == 0
            and counts[EventType.OBJECT_EXITED] == 0
            and counts[EventType.CROSSING_DIRECTION] == 0
            and counts[EventType.DWELL_TIME] == 0,
        ),
        ("engine state pruned to empty", len(engine._known_ids) == 0),
    ]
    ok = _report(checks)

    # Idempotent reset must clear everything.
    engine.reset()
    if engine._meta_by_id or engine._known_ids:
        print("  [FAIL] reset() left residual state")
        ok = False
    else:
        print("  [PASS] reset() clears state")

    return ok


def _build_geometry_timeline() -> list[list[Detection]]:
    """
    One truck travels left->right along y=100 for frames 0..12, then leaves so
    the track is deleted. Its path takes it through the 'dock' zone (x in
    ~[195,305]) and across the vertical 'gate' line at x=250.
    """
    timeline: list[list[Detection]] = []
    for f in range(0, 17):
        dets: list[Detection] = []
        if 0 <= f <= 12:
            dets.append(_det(100 + 20 * f, 100, "truck"))
        timeline.append(dets)
    return timeline


def run_geometry() -> bool:
    """Scenario 2: entered / exited / crossing-direction / dwell."""
    tracker = CentroidTracker(
        TrackingConfig(centroid_distance_threshold=50.0, max_age=3)
    )
    zone = Zone(
        name="dock",
        polygon=((195, 50), (305, 50), (305, 150), (195, 150)),
    )
    # Vertical gate: left side is "positive". Moving rightward ends on the
    # right (negative) side, so a left->right crossing is labelled "right".
    gate = CrossingLine(
        name="gate",
        start=(250, 0),
        end=(250, 200),
        positive_label="left",
        negative_label="right",
    )
    engine = EventEngine(
        stream_id="selftest-geometry",
        config=EventEngineConfig(
            stationary_min_frames=5,
            zones=(zone,),
            lines=(gate,),
            dwell_seconds=0.1,
        ),
    )

    all_events = []
    for frame_id, dets in enumerate(_build_geometry_timeline()):
        result = tracker.update(dets, frame_id, frame_id / 30.0)
        all_events.extend(engine.process(result))

    counts = Counter(e.event_type for e in all_events)
    _print_events("Derived events:", all_events)
    print("Counts:", {k.value: v for k, v in counts.items()})

    crossings = [e for e in all_events if e.event_type is EventType.CROSSING_DIRECTION]
    dwells = [e for e in all_events if e.event_type is EventType.DWELL_TIME]
    entries = [e for e in all_events if e.event_type is EventType.OBJECT_ENTERED]

    checks = [
        ("one zone entry", counts[EventType.OBJECT_ENTERED] == 1),
        ("one zone exit", counts[EventType.OBJECT_EXITED] == 1),
        ("entry carries zone name", all(e.metadata.get("zone") == "dock" for e in entries)),
        ("one line crossing", len(crossings) == 1),
        (
            "crossing direction is 'right'",
            len(crossings) == 1 and crossings[0].metadata.get("direction") == "right",
        ),
        ("crossing names the line", all(e.metadata.get("line") == "gate" for e in crossings)),
        ("one dwell event", len(dwells) == 1),
        ("dwell is HIGH severity", all(e.severity is Severity.HIGH for e in dwells)),
        (
            "dwell reports time inside",
            all(e.metadata.get("dwell_seconds", 0) >= 0.1 for e in dwells),
        ),
        ("entered/exited are LOW severity", all(
            e.severity is Severity.LOW
            for e in all_events
            if e.event_type in (EventType.OBJECT_ENTERED, EventType.OBJECT_EXITED)
        )),
        ("crossing is MEDIUM severity", all(e.severity is Severity.MEDIUM for e in crossings)),
        ("not reported stationary while moving", counts[EventType.STATIONARY_OBJECT] == 0),
        ("track appeared once", counts[EventType.OBJECT_APPEARED] == 1),
        ("track disappeared once", counts[EventType.OBJECT_DISAPPEARED] == 1),
        ("engine state pruned to empty", len(engine._known_ids) == 0),
    ]
    ok = _report(checks)

    engine.reset()
    return ok


def run() -> int:
    print("=" * 64)
    print("SCENARIO 1 — lifecycle (appeared/disappeared/stationary/collision)")
    print("=" * 64)
    ok_lifecycle = run_lifecycle()

    print("\n" + "=" * 64)
    print("SCENARIO 2 — geometry (entered/exited/crossing/dwell)")
    print("=" * 64)
    ok_geometry = run_geometry()

    ok = ok_lifecycle and ok_geometry
    print("\n" + ("ALL CHECKS PASSED" if ok else "SELF-TEST FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(run())
