# Requirements: OmniTrack AI Platform

## Functional Requirements

### 1. Ingestion & Stream Management
- [x] **REQ-ING-1**: Support RTSP, webcam, HTTP/MJPEG, video file, and synthetic test streams.
- [x] **REQ-ING-2**: Provide stream lifecycle APIs to add, start, stop, restart, and delete stream configs.
- [x] **REQ-ING-3**: Automatically detect stream disconnects and attempt exponential backoff reconnection.

### 2. Real-Time Computer Vision & Tracking
- [x] **REQ-CV-1**: Run YOLOv8 object detection with configurable confidence thresholds and class filtering.
- [x] **REQ-CV-2**: Assign and maintain persistent track IDs across consecutive frames using Centroid Tracking.
- [x] **REQ-CV-3**: Support runtime model switching between PyTorch and ONNX Runtime backends.
- [ ] **REQ-CV-4**: Integrate TensorRT acceleration for GPU-accelerated sub-20ms inference pipelines.

### 3. Spatial Rules & Event Detection
- [x] **REQ-EVT-1**: Define polygon zones and detect object occupancy, dwell times, and unauthorized entries.
- [x] **REQ-EVT-2**: Define directional crossing lines and calculate bidirectional entry/exit counts.
- [x] **REQ-EVT-3**: Publish structured events and alerts to Kafka and persist them to relational storage.

### 4. Telemetry & Observability
- [x] **REQ-OBS-1**: Calculate real-time FPS, inference duration (ms), frame drop rate, and CPU/memory usage.
- [x] **REQ-OBS-2**: Expose Prometheus metrics endpoint (`/metrics`) and Kubernetes readiness probes (`/readyz`).
- [x] **REQ-OBS-3**: Provide structured JSON logs with correlation IDs for tracing stream execution.

### 5. Web Dashboard & Operator UI
- [x] **REQ-UI-1**: Deliver low-latency live video streaming via WebSocket to HTML5 canvas.
- [x] **REQ-UI-2**: Display real-time bounding boxes, track labels, and trajectory paths.
- [x] **REQ-UI-3**: Interactive multi-stream grid view and detailed single-stream inspector.
- [x] **REQ-UI-4**: Visual zone and line configuration tools in the web UI.

### 6. Security & Multi-Tenancy
- [x] **REQ-SEC-1**: JWT-based user authentication and token refresh handling.
- [x] **REQ-SEC-2**: Role-based access control (Admin, Operator, Viewer).

---

## Non-Functional Requirements & SLOs
- **NFR-LAT**: End-to-end processing latency < 50ms per frame under nominal load.
- **NFR-CONC**: Capable of processing 50+ concurrent HD (1080p) streams in a distributed cluster.
- **NFR-MEM**: Bounded memory execution preventing leaks over 24/7 continuous stream operations.
- **NFR-AVAIL**: 99.9% availability with automatic failover via Redis leases and Kubernetes replica sets.
