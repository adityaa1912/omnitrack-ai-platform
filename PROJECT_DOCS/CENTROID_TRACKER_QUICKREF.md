# Centroid Tracker Quick Reference

## Current commands

```bash
# Tracking enabled (default)
python -m inference.main --source 0 --conf 0.5 --device cpu

# Balanced defaults
python -m inference.main --source 0 --track-dist 50 --max-age 30

# Loose matching: crowded scene / moving camera
python -m inference.main --source 0 --track-dist 100 --max-age 15

# Tight matching: stationary camera / sparse scene
python -m inference.main --source 0 --track-dist 30 --max-age 60 --no-trajectories

# Detection only
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

config = TrackingConfig(centroid_distance_threshold=40.0, max_age=45)
frame_source_config = FrameSourceConfig(source=0, width=640, height=480, fps=30)
detector_config = DetectorConfig(model_name="yolov8n.pt", device="cpu")
visualizer_config = VisualizerConfig()

setup_logging("INFO", False)

with get_frame_source(frame_source_config) as frame_source, \
     Detector(detector_config) as detector:
    visualizer = Visualizer(visualizer_config)
    fps_counter = FpsCounter()
    tracker = CentroidTracker(config)

    for frame in frame_source.read():
        result = detector.predict(frame)
        fps_counter.update(result.inference_time_ms)

        tracking_result = tracker.update(
            result.detections, frame.frame_id, frame.timestamp
        )
        output_frame = visualizer.render_tracked(
            frame.data, tracking_result, fps=fps_counter.fps
        )
        # render or stream output_frame here
```

## Parameter guide

| Parameter | CLI | Default | Meaning |
|-----------|-----|---------|---------|
| Centroid distance threshold | `--track-dist` | `50` pixels | Max distance to match detection-to-track |
| Max age | `--max-age` | `30` frames | Delete lost track after this many frames |

Too small → tracks lost quickly; too large → ID swaps in crowded scenes.

## What is not supported

No `--track-conf`, `--track-buffer`, `ByteTracker`, `show_trajectories` config field, or arbitrary webcam index via `--source 1` (factory supports camera index `0`, local files, and `rtsp://`/`rtsps://` only).

## Verification

Run the synthetic tracker/event self-check — no camera or model required:
```
python -m inference.events._selftest
```
