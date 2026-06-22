"""
Scene region definitions: zones (polygons) and crossing lines.

These are the user-facing geometry configuration objects referenced by
`EventEngineConfig`. They are immutable and self-validating, and wrap the pure
functions in `geometry.py` so detectors interact with named regions rather than
raw point math.

Coordinates are in the same pixel space as detections (top-left origin).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .geometry import Point, crossing_sign, point_in_polygon


@dataclass(frozen=True)
class Zone:
    """A named polygonal area. Membership is tested against detection centroids."""

    name: str
    polygon: tuple[Point, ...]

    def __post_init__(self) -> None:
        # Coerce any point sequence (lists, etc.) into immutable tuples.
        coerced = tuple((float(x), float(y)) for x, y in self.polygon)
        object.__setattr__(self, "polygon", coerced)
        if len(coerced) < 3:
            raise ValueError(f"Zone {self.name!r} needs >= 3 vertices")

    def contains(self, point: Point) -> bool:
        return point_in_polygon(point, self.polygon)


@dataclass(frozen=True)
class CrossingLine:
    """
    A named directed line segment for crossing detection.

    `positive_label` / `negative_label` name the two crossing directions:
    `positive_label` is reported when motion ends on the LEFT of start->end,
    `negative_label` when it ends on the RIGHT. Choosing the start/end order
    fixes which physical direction each label means.
    """

    name: str
    start: Point
    end: Point
    positive_label: str = "positive"
    negative_label: str = "negative"

    def __post_init__(self) -> None:
        start = (float(self.start[0]), float(self.start[1]))
        end = (float(self.end[0]), float(self.end[1]))
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        if start == end:
            raise ValueError(f"CrossingLine {self.name!r} needs distinct endpoints")

    def direction_of(self, prev: Point, cur: Point) -> Optional[str]:
        """Label of the crossing for motion prev->cur, or None if no crossing."""
        sign = crossing_sign(prev, cur, self.start, self.end)
        if sign is None:
            return None
        return self.positive_label if sign > 0 else self.negative_label
