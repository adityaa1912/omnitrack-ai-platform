# Technology Stack

## Languages & Runtimes
- **Python**: 3.11+ (Backend API, Inference Engine, ML Workers)
- **TypeScript**: 5.6.2 (Frontend application, strict mode enabled)
- **Node.js**: 20+ runtime for frontend tooling & Vite dev server
- **SQL / Relational**: PostgreSQL (production), SQLite (local dev & testing)

## Core Frameworks & Libraries

### Backend & API
- **FastAPI**: 0.104.1 — RESTful endpoints, streaming WebSockets, dependency injection, role-based auth middleware
- **Uvicorn**: 0.24.0 — ASGI server with uvloop support
- **Pydantic & Pydantic-Settings**: v2 (2.5.0 / 2.1.0) — Schema validation, typed configuration, and environment parsing
- **SQLAlchemy**: 2.0.23 — Declarative ORM and async-ready SQL expression engine
- **Alembic**: 1.18.5 — Database schema migrations
- **Psycopg**: 3.3.4 (binary) — High-performance PostgreSQL driver with connection pooling

### Machine Learning & Computer Vision
- **Ultralytics YOLOv8**: 8.4.54 — Object detection, tracking, instance segmentation
- **PyTorch & Torchvision**: torch 2.12.0, torchvision 0.27.0 — Deep learning backend runtime
- **ONNX Runtime**: 1.24.1 — Optimized cross-platform inference engine with ONNX model support
- **OpenCV (opencv-python)**: 4.13.0.92 — Video stream decoding (RTSP, webcam, file), frame transformations, image processing
- **NumPy & SciPy**: 2.4.6 / 1.17.1 — Matrix computations, vector geometry, centroid tracking distance calculations
- **Pillow**: 12.2.0 — Image decoding and manipulation

### Distributed Systems & Streaming
- **Redis (redis-py)**: 5.2.1 — Distributed stream leases, distributed caching, pub/sub signaling
- **Confluent Kafka**: 2.15.0 — High-throughput event streaming, detection frame events, dead-letter topics
- **Polars**: 1.41.0 — High-performance columnar analytics, metric rollups, and time-series aggregations

### Observability & Metrics
- **Prometheus Client**: 0.21.1 — Exporting operational metrics (`/metrics`), inference latency, frame drops, GPU/CPU utilization
- **psutil**: 7.2.2 — System-level telemetry (memory, CPU, disk IO)

### Frontend
- **React**: 18.3.1 — User interface library with Concurrent Mode features
- **Vite**: 7.3.6 — Build tool, HMR, bundling
- **Zustand**: 5.0.14 — High-performance client state management isolated from hot rendering loops
- **TanStack React Query**: 5.101.0 — Server state caching, background polling, mutations
- **React Router**: 6.30.4 — Client-side declarative routing
- **Tailwind CSS**: 3.4.13 — Utility-first styling with custom dark theme palette
- **Recharts**: 3.8.1 — Metric visualizations, telemetry graphs
- **Lucide React**: 0.451.0 — Modern iconography

## Containerization & Orchestration
- **Docker**: Multi-stage Dockerfiles (`Dockerfile.backend`, `Dockerfile.frontend`)
- **Docker Compose**: Orchestration for local development stack (Backend, Frontend, Postgres, Redis, Kafka, Prometheus)
- **Kubernetes**: Production manifests with Deployments, StatefulSets/PVC, Services, ConfigMaps, Secrets, and HPA (Horizontal Pod Autoscaling)
