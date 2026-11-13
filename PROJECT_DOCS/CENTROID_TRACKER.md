# Centroid-Based Tracker — Current Implementation

## Overview

OmniTrack uses `CentroidTracker` in `inference/tracker.py`: a pure-Python centroid matcher with no external tracking library. It is not 100% dependency-free (it imports NumPy), and its speed/accuracy figures are unverified claims — measure with `scripts/perf/run_baseline.py` if you need data.

## Configuration

`TrackingConfig`:
```python
TrackingConfig(
    enabled=True,
    centroid_distance_threshold=50.0,   # pixels
    max_age=30,                         # frames before deleting lost tracks
    max_trajectory_points=100,
)
```

CLI:
```
python -m inference.main --source 0 --track-dist 50 --max-age 30
python -m inference.main --source 0 --no-tracking
```

## Correct programmatic example

```python
from inference import (
    CentroidTracker,
    TrackingConfig,
    Detector,
    Visualizer,
    FpsCounter,
    setup_logging,
)
from inference.config import FrameSourceConfig, DetectorConfig, VisualizerConfig
from inference.frame_source import get_frame_source

setup_logging("INFO", False)

config = TrackingConfig(centroid_distance_threshold=40.0, max_age=45)
frame_source_config = FrameSourceConfig(source=0, width=640, height=480, fps=30)
detector_config = DetectorConfig(model_name="yolov8n.pt", device="cpu")
visualizer_config = VisualizerConfig()

with get_frame_source(frame_source_config) as frame_source, \
     Detector(detector_config) as detector:
    visualizer = Visualizer(visualizer_config)
    fps_counter = FpsCounter()
    tracker = CentroidTracker(config)

    for frame in frame_source.read():
        result = detector.predict(frame)
        fps_counter.update(result.inference_time_ms)

        if tracker is not None:
            tracking_result = tracker.update(
                result.detections, frame.frame_id, frame.timestamp
            )
            output_frame = visualizer.render_tracked(
                frame.data, tracking_result, fps=fps_counter.fps
            )
        else:
            output_frame = visualizer.render(
                frame.data, result, fps=fps_counter.fps
            )

        # stream output_frame however your UI needs it
```

All constructors require their config objects: `Detector(detector_config)`, `Visualizer(visualizer_config)`, `CentroidTracker(tracking_config)`, and `get_frame_source(frame_source_config)`.

## Behavior notes

- Greedy nearest-centroid matching; IDs start at 0 per process.
- Lost tracks are deleted when `times_disappeared > max_age`.
- `TrackingResult.get_active_objects()` includes lost tracks holding old detections; use `not obj.is_lost` for active-only behavior.
- Region coordinates are detector-space pixels; optional inference resize does not remap boxes back to original-frame coordinates.

## Not supported

No ByteTrack, Kalman prediction, appearance matching, or Hungarian assignment. If you need those, implement a new `Tracker` subclass and export it from `inference/__init__.py`.
