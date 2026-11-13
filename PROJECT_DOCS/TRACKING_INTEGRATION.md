# Multi-Object Tracking Integration - Implementation Guide

## Overview

This guide describes the **current** tracking implementation in OmniTrack: `CentroidTracker` in `inference/tracker.py`. It replaces the earlier ByteTrack guide; `ByteTracker`, `track_high_thresh`, `match_thresh`, and related ByteTrack APIs do not exist in this codebase.

Tracking is lightweight, pure Python, optional, and enabled by default. It runs inside both the standalone `inference.main` GUI and the FastAPI backend pipeline (`backend/service.py`).

## Current implementation

- `inference/tracker.py` — `Tracker` ABC plus `CentroidTracker`
- `inference/tracking_config.py` — `TrackingConfig`
- `inference/tracking_types.py` — `TrackedObject`, `TrackingResult`, `TrajectoryPoint`
- `inference/__init__.py` — exports `Tracker`, `CentroidTracker`, `TrackingConfig`, `TrackedObject`, `TrackingResult`, `TrajectoryPoint`

### `TrackingConfig`

```python
TrackingConfig(
    enabled: bool = True,
    centroid_distance_threshold: float = 50.0,  # pixels
    max_age: int = 30,                          # frames before deleting lost tracks
    max_trajectory_points: int = 100,
)
```

### CLI arguments (`python -m inference.main`)

- `--track-dist 50.0` — centroid distance threshold in pixels
- `--max-age 30` — frames before deleting lost tracks
- `--no-tracking` — disable tracking
- `--no-trajectories` — hide trajectory visualization

### Matching and lifecycle

Matching is greedy nearest-centroid Euclidean distance, one detection to one track. Lost tracks are deleted when `times_disappeared > max_age` (strictly greater, not at equality). `TrackingResult.get_active_objects()` returns objects with a current detection; use `not obj.is_lost` when active-only behavior matters. Track IDs start at 0 and reset per process.

### Events and regions

Tracking must be enabled for event derivation and region processing. `inference/events/` derives appearance/disappearance, stationary, near-collision, zone entry/exit/dwell, and directional crossing events synchronously during frame processing. Live region updates replace geometry without restarting a stream.

## Usage

```bash
# Tracking enabled (default)
python -m inference.main --source 0 --conf 0.5 --device cpu

# Tight matching for a stationary camera
python -m inference.main --track-dist 30 --max-age 60

# Loose matching for crowded scenes
python -m inference.main --track-dist 100 --max-age 15

# Detection only
python -m inference.main --source 0 --no-tracking
```

Programmatic entry points construct configs explicitly:

```python
from inference import CentroidTracker, TrackingConfig

config = TrackingConfig(centroid_distance_threshold=40.0, max_age=45)
with CentroidTracker(config) as tracker:
    ...
```

## What is not here

- No ByteTrack library, BoT-SORT, Kalman filtering, appearance matching, or Hungarian assignment.
- No multi-stream tracker service; the backend schedules per-stream inference independently.
- Performance figures in older docs are unverified claims; measure with `scripts/perf/run_baseline.py` if needed.
