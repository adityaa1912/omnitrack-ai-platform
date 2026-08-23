from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

OBJECT_COUNT_THRESHOLD = "object_count_threshold"
ZONE_OCCUPANCY_THRESHOLD = "zone_occupancy_threshold"
DWELL_TIME_THRESHOLD = "dwell_time_threshold"
LINE_CROSSING_THRESHOLD = "line_crossing_threshold"
EVENT_TYPE_MATCH = "event_type_match"
CLASS_SPECIFIC = "class_specific"

RULE_TYPES = frozenset(
    {
        OBJECT_COUNT_THRESHOLD,
        ZONE_OCCUPANCY_THRESHOLD,
        DWELL_TIME_THRESHOLD,
        LINE_CROSSING_THRESHOLD,
        EVENT_TYPE_MATCH,
        CLASS_SPECIFIC,
    }
)

OPERATORS = frozenset({"gte", "gt", "lte", "lt", "eq", "ne"})

CONDITION_LOGIC = frozenset({"AND", "OR"})


def compare(value: float, operator: str, threshold: float) -> bool:
    if operator == "gte":
        return value >= threshold
    if operator == "gt":
        return value > threshold
    if operator == "lte":
        return value <= threshold
    if operator == "lt":
        return value < threshold
    if operator == "eq":
        return value == threshold
    if operator == "ne":
        return value != threshold
    return False


def _object_count(snapshot: Dict[str, Any], class_name: Optional[str]) -> float:
    counts = snapshot.get("object_counts", {}) or {}
    if class_name:
        return float(counts.get(class_name, 0))
    return float(sum(counts.values()))


def _zone_occupancy(snapshot: Dict[str, Any], zone_name: Optional[str]) -> float:
    zones = snapshot.get("zone_occupancy", {}) or {}
    if zone_name:
        stats = zones.get(zone_name, {}) or {}
        return float(stats.get("entries", 0)) - float(stats.get("exits", 0))
    total = 0.0
    for stats in zones.values():
        total += float(stats.get("entries", 0)) - float(stats.get("exits", 0))
    return total


def _dwell_seconds(snapshot: Dict[str, Any], zone_name: Optional[str], event: Optional[Dict[str, Any]]) -> float:
    if event is not None:
        meta = event.get("metadata", {}) or {}
        if "seconds" in meta:
            return float(meta.get("seconds", 0.0))
    zones = snapshot.get("zone_occupancy", {}) or {}
    if zone_name:
        stats = zones.get(zone_name, {}) or {}
        return float(stats.get("dwell_seconds", 0.0))
    return float(snapshot.get("dwell_time_total_seconds", 0.0))


def _line_crossings(snapshot: Dict[str, Any], line_name: Optional[str]) -> float:
    lines = snapshot.get("line_crossings", {}) or {}
    if line_name:
        stats = lines.get(line_name, {}) or {}
        return float(stats.get("positive", 0)) + float(stats.get("negative", 0))
    total = 0.0
    for stats in lines.values():
        total += float(stats.get("positive", 0)) + float(stats.get("negative", 0))
    return total


def evaluate_condition(
    condition: Dict[str, Any],
    snapshot: Dict[str, Any],
    event: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Any]:
    ctype = condition.get("type")
    operator = condition.get("operator", "gte")
    threshold = condition.get("threshold", 0)

    if ctype == OBJECT_COUNT_THRESHOLD:
        value = _object_count(snapshot, condition.get("class_name"))
        return compare(value, operator, threshold), value

    if ctype == ZONE_OCCUPANCY_THRESHOLD:
        value = _zone_occupancy(snapshot, condition.get("zone_name"))
        return compare(value, operator, threshold), value

    if ctype == DWELL_TIME_THRESHOLD:
        value = _dwell_seconds(snapshot, condition.get("zone_name"), event)
        return compare(value, operator, threshold), value

    if ctype == LINE_CROSSING_THRESHOLD:
        value = _line_crossings(snapshot, condition.get("line_name"))
        return compare(value, operator, threshold), value

    if ctype == EVENT_TYPE_MATCH:
        if event is None:
            return False, None
        matched = event.get("event_type") == condition.get("event_type")
        return bool(matched), event.get("event_type")

    if ctype == CLASS_SPECIFIC:
        target = condition.get("class_name")
        event_class = event.get("class_name") if event else None
        if "threshold" in condition and condition.get("threshold") is not None and event is None:
            value = _object_count(snapshot, target)
            return compare(value, operator, threshold), value
        matched = event_class == target
        return bool(matched), event_class

    return False, None


def evaluate_rule(
    conditions: List[Dict[str, Any]],
    logic: str,
    snapshot: Dict[str, Any],
    event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    logic = (logic or "AND").upper()
    if not conditions:
        return {"matched": False, "matched_conditions": [], "computed_values": {}}

    results = []
    computed: Dict[str, Any] = {}
    for index, condition in enumerate(conditions):
        matched, value = evaluate_condition(condition, snapshot, event)
        results.append(matched)
        computed[str(condition.get("type", index))] = value

    if logic == "OR":
        overall = any(results)
    else:
        overall = all(results)

    matched_conditions = [
        conditions[i] for i, matched in enumerate(results) if matched
    ]
    return {
        "matched": overall,
        "matched_conditions": matched_conditions,
        "computed_values": computed,
    }
