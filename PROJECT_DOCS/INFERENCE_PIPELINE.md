# YOLOv8 Real-Time Inference Pipeline — Current State

## Overview

`inference/main.py` is a standalone OpenCV GUI pipeline for local development and verification: capture → detector/provider → optional centroid tracking → renderer → display. It is **not** the API backend, and the backend does not call `inference.main`. `backend/service.py` independently composes the same inference modules (detector, source, tracker, visualizer, event engine) per stream.

## Entry point

```
python -m inference.main --source 0 --conf 0.5 --device cpu
```

Defaults: `yolov8n.pt`, Torch provider, CPU, confidence 0.5, IoU 0.45, capture 640×480 at requested 30 FPS, tracking enabled, matching distance 50 px, max age 30 processed updates. `q` or SIGINT/SIGTERM requests shutdown.

## Current module map

- `types.py` — `Frame`, `Detection`, `InferenceResult`
- `config.py` — `DetectorConfig`, `FrameSourceConfig`, `VisualizerConfig`, `TrackingConfig`, `AppConfig`, CLI parsing
- `logger.py` — plain timestamp/name/level/message logging (not JSON/structured)
- `detector.py` — `Detector`: Torch/ONNX/OpenVINO/TensorRT providers, provider auto-selector
- `frame_source.py` — `WebcamFrameSource`, `VideoFileFrameSource`, `RTSPFrameSource`, `get_frame_source` factory
- `tracker.py` — `Tracker` ABC and `CentroidTracker` (greedy nearest-centroid matching; not ByteTrack/Kalman/Hungarian)
- `tracking_types.py` — `TrackedObject`, `TrackingResult`, `TrajectoryPoint`
- `visualizer.py` — `Visualizer`, `FpsCounter` (EMA of inference milliseconds, not end-to-end throughput)
- `events/` — event engine, geometry, regions, appearance/stationary/proximity/zone/line detectors
- `frame_pool.py` — process-wide NumPy buffer reuse, disabled by default

## Contracts and limitations

- `Detector(detector_config)`, `Visualizer(visualizer_config)`, `CentroidTracker(tracking_config)`, and `get_frame_source(frame_source_config)` all require config objects — none has a no-arg constructor.
- `get_frame_source` supports camera index `0`, existing local files, and `rtsp://`/`rtsps://`. Arbitrary webcam indices and HTTP URLs are not factory-supported; `--source 1` does not open camera 1.
- Webcam failed reads loop indefinitely while the device stays open; there is no guaranteed eventual exit. RTSP reconnects with capped exponential backoff; timeout checks sit outside blocking `VideoCapture.read`, so they do not impose a hard I/O deadline.
- Detection coordinates are in provider-input space; optional inference resizing does **not** remap boxes back to original-frame coordinates.
- `FpsCounter` measures inference time only, not capture/render throughput.
- Standalone main has no API, database, event engine, headless mode, or model warmup guarantee (first frames can be slow).
- Provider names: `torch`, `onnx`, `openvino`, `tensorrt`, `auto`. Explicit provider load failures propagate; auto tries TensorRT → OpenVINO → ONNX → torch. Normal dependencies include ONNX Runtime but not OpenVINO or GPU TensorRT runtime.

## Verification

Run without camera or model:
```
python -m inference.events._selftest
```

Backend integration is separate: `backend/service.py` constructs detector/source/tracker/visualizer per stream, optionally with scheduler, batching, model manager, and recording manager. Changes to `inference/` affect both pipelines.

## Not here

No Phase 2/3/4/5 roadmap items are implemented in this directory: no Kafka frame source, no TensorRT service replacing the detector, no FastAPI wrapping of this main, no OpenTelemetry, no headless mode. Deployment exists in `backend/main.py`, `Dockerfile.*`, `docker-compose.yml`, and `kubernetes/`.
