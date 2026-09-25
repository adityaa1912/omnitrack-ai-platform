# System Architecture

## Architecture Overview
OmniTrack is a distributed, real-time AI video analytics system engineered for low-latency multi-stream processing, computer vision inference, track persistence, and live telemetry visualization.

```
                    [ Cameras / RTSP Streams / Webcams ]
                                    │
                                    ▼
                     ┌─────────────────────────────┐
                     │     Frame Capture Worker    │ (cv2 / Threaded FrameSource)
                     └─────────────────────────────┘
                                    │ (Zero-copy FramePool)
                                    ▼
                     ┌─────────────────────────────┐
                     │   Inference Engine (YOLO)   │ (Ultralytics / ONNX Runtime)
                     └─────────────────────────────┘
                                    │
                                    ▼
                     ┌─────────────────────────────┐
                     │   Multi-Object Tracker      │ (Centroid Tracker / Hungarian algorithm)
                     └─────────────────────────────┘
                                    │
                                    ▼
                     ┌─────────────────────────────┐
                     │   Spatial & Rule Engine     │ (Zones, Crossing Lines, Anomaly)
                     └──────────────┬──────────────┘
                                    │
           ┌────────────────────────┴────────────────────────┐
           ▼                                                 ▼
┌─────────────────────────────┐                   ┌─────────────────────────────┐
│   Kafka / Redis Streams     │                   │   FastAPI WebSocket Hub     │
│   (Event Bus & Persistence) │                   │   (Broadcast to UI clients) │
└─────────────────────────────┘                   └──────────────┬──────────────┘
                                                                 │
                                                                 ▼
                                                  ┌─────────────────────────────┐
                                                  │   React 18 Dashboard        │
                                                  │   Canvas + RAF Render Loop  │
                                                  │   Zustand Telemetry Store   │
                                                  └─────────────────────────────┘
```

## Architectural Patterns & Core Subsystems

### 1. Zero-Copy Frame Pool (`inference/frame_pool.py`)
- Pre-allocates fixed-size NumPy buffers to prevent memory fragmentation and GC pauses during high-FPS video streaming.
- Bounded queue prevents buffer bloat; older unconsumed frames are dropped gracefully to preserve real-time low latency.

### 2. Multi-Threaded Inference Pipeline (`backend/service.py`, `inference/detector.py`)
- Independent capture threads isolate RTSP I/O wait times from model execution.
- Dynamic frame batching (`backend/batching.py`) batches frames across active streams when GPU inference is saturated.
- Supports hot-swapping between YOLOv8 PyTorch and ONNX Runtime models via `ModelManager`.

### 3. Object Tracking & Spatial Event Analysis (`inference/tracker.py`, `inference/events/`)
- Persistent object identities maintained across frames using Centroid Tracking with Euclidean distance cost matrices.
- Spatial rules:
  - **Polygon Zones**: Detects dwell time, unauthorized intrusions, occupancy counts.
  - **Crossing Lines**: Directional vectors counting enter/exit events.

### 4. Distributed Coordination & Resilience (`backend/ownership/`, `backend/observability/`)
- **Stream Leases**: Leader election and distributed leases via Redis ensure each video stream is processed by exactly one worker instance in a clustered deployment.
- **Worker Supervisor**: Monitors thread health, restarts crashed video readers, and enforces graceful shutdown hooks.

### 5. Frontend Decoupled Rendering Architecture (`frontend/src/`)
- Frame hot-path is completely isolated from React's virtual DOM reconciliation.
- Frames are decoded via `createImageBitmap` and drawn directly to an HTML5 `<canvas>` inside a `requestAnimationFrame` loop.
- Zustand store handles telemetry, FPS counters, and detection lists without triggering full-page component re-renders.
