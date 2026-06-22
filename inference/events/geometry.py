"""
Pure 2D geometry primitives for the event layer.

Stateless, dependency-free functions operating on `(x, y)` pixel coordinates in
the same frame space as detections. Kept separate from the region/zone value
types and from the detectors so the math can be reasoned about and tested in
isolation.
"""

from __future__ import annotations

from typing import Optional, Sequence

Point = tuple[float, float]


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """
    Even-odd (ray casting) point-in-polygon test.

    The polygon is an ordered sequence of >= 3 vertices; the edge from the last
    vertex back to the first is implicit. Boundary cases are intentionally not
    specially handled — callers use slightly padded polygons rather than relying
    on exact-edge semantics.
    """
    x, y = point
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        denom = (yj - yi) or 1e-12
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / denom + xi):
            inside = not inside
        j = i
    return inside


def side(a: Point, b: Point, p: Point) -> float:
    """
    Signed area (2x) of triangle (a, b, p) == cross product (b - a) x (p - a).

    > 0 : p is left of the directed line a->b
    < 0 : p is right
    == 0: p is collinear with a->b
    """
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _orientation(a: Point, b: Point, c: Point) -> int:
    v = side(a, b, c)
    if v > 0:
        return 1
    if v < 0:
        return -1
    return 0


def _on_segment(a: Point, b: Point, p: Point) -> bool:
    """Whether collinear point p lies within the bounding box of segment a-b."""
    return (
        min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])
    )


def segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    """Whether segment p1-p2 intersects segment p3-p4 (incl. collinear touch)."""
    o1 = _orientation(p1, p2, p3)
    o2 = _orientation(p1, p2, p4)
    o3 = _orientation(p3, p4, p1)
    o4 = _orientation(p3, p4, p2)

    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(p1, p2, p3):
        return True
    if o2 == 0 and _on_segment(p1, p2, p4):
        return True
    if o3 == 0 and _on_segment(p3, p4, p1):
        return True
    if o4 == 0 and _on_segment(p3, p4, p2):
        return True
    return False


def crossing_sign(prev: Point, cur: Point, line_start: Point, line_end: Point) -> Optional[int]:
    """
    Direction in which the motion prev->cur crosses the directed line.

    Returns:
        +1  if motion ends on the left  (positive) side of line_start->line_end
        -1  if motion ends on the right (negative) side
        None if the motion does not strictly cross the line *segment*

    A crossing requires prev and cur to be on strictly opposite sides AND the
    motion segment to actually intersect the (finite) line segment, so motion
    that passes the infinite line but misses the drawn extent is not counted.
    """
    s_prev = side(line_start, line_end, prev)
    s_cur = side(line_start, line_end, cur)
    if s_prev * s_cur >= 0:
        return None
    if not segments_intersect(prev, cur, line_start, line_end):
        return None
    return 1 if s_cur > 0 else -1
