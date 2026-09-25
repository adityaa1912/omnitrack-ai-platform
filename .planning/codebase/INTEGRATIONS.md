# System Integrations

## Ingestion & Protocol Protocols
- **RTSP / HTTP / MJPEG**: Video streams decoded via OpenCV `cv2.VideoCapture` inside isolated reader threads. Supports hardware-accelerated decode where configured.
- **Webcams & Local Media**: Integrated local webcam capture (indices 0, 1, etc.) and pre-recorded synthetic/test video sources for QA.
- **WebSockets**: Real-time bidirectional streaming between FastAPI backend (`/ws/streams/{stream_id}`) and React frontend. Delivers base64/binary JPEG frames, active track metadata, FPS, and detection bounding boxes.

## Data Storage & Databases
- **PostgreSQL / SQLAlchemy**:
  - Primary persistent data store for stream configurations, user credentials, detection logs, and audit trails.
  - SQLite supported as fallback for single-node local development and test environments (`dev.db`, `inference_data.db`).
- **Redis**:
  - Distributed lease management (`LeaseManager` / `LeaderLease`) for stream ownership across multiple replicas.
  - Hot caching (`JsonCache`) for rapid stream state reads and telemetry buffering.
- **Confluent Kafka**:
  - Ingestion & Event bus: Topics for `omnitrack.frames`, `omnitrack.events`, `omnitrack.alerts`.
  - Enables asynchronous decoupling of inference detection from downstream alerting, archiving, and persistent reporting.

## Observability & Monitoring
- **Prometheus**:
  - Metrics exported at `/metrics` endpoint via `prometheus_client`.
  - Exposes counters, histograms, and gauges for frame processing latency, inference latency, drop rate, active stream count, and system memory.
- **Structured JSON Logging**:
  - Contextual logging with correlation IDs (`correlation.py`) across HTTP requests, WebSocket sessions, and inference worker threads.
- **Readiness & Liveness Probes**:
  - Kubernetes health probes integrated at `/healthz` and `/readyz`, performing deep checks on database, Redis connection, and worker supervisor status.

## Frontend & Backend Contracts
- **REST API**:
  - `/api/v1/streams`: Stream CRUD, start, stop, restart, dynamic config tuning.
  - `/api/v1/auth`: JWT authentication, token refresh, user role checking.
  - `/api/v1/analytics`: Aggregate statistics, time-series counts, detection heatmaps.
  - `/api/v1/events`: Zone crossing events, trigger logs, alert acknowledgment.
- **WebSocket Protocol**:
  - Frame delivery format: JSON payload containing timestamp, stream_id, sequence, fps, inference_ms, detections array, and image data (JPEG).
