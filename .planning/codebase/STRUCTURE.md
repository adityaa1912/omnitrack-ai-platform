# Codebase Structure

## Directory Tree

```
project-root/
├── backend/                  # FastAPI web server and backend orchestration
│   ├── alerts/               # Alert rules, thresholds, and notification delivery
│   ├── analytics/            # Statistical aggregation, metrics rollup, Polars analysis
│   ├── auth/                 # JWT token management, password hashing, RBAC dependencies
│   ├── cache/                # Redis caching and lease helpers
│   ├── inference/            # Backend-level model management & batching coordinators
│   ├── messaging/            # Kafka event producers, consumers, and message contracts
│   ├── observability/        # Prometheus metrics, structured logging, supervisor, probes
│   ├── ownership/            # Distributed leader election & lease manager
│   ├── recording/            # Video segment storage and MP4/HLS packaging
│   ├── batching.py           # Multi-stream frame batcher
│   ├── event_store.py        # Detection persistence layer (SQLAlchemy)
│   ├── main.py               # FastAPI application entrypoint, routers, WebSocket handlers
│   ├── model_manager.py      # Runtime model loader (PyTorch, ONNX)
│   ├── models.py             # SQLAlchemy ORM database models
│   ├── run.py                # Server launcher script
│   ├── scheduler.py          # Stream scheduler and task runner
│   ├── service.py            # InferenceService orchestrating stream lifecycles
│   └── settings.py           # Pydantic BaseSettings environment configuration
│
├── inference/                # Computer vision & machine learning engine
│   ├── events/               # Spatial geometry: Zone polygons and CrossingLine logic
│   ├── config.py             # Inference configuration datatypes
│   ├── detector.py           # YOLOv8 / ONNX inference wrapper
│   ├── frame_pool.py         # Zero-copy memory pool for video frames
│   ├── frame_source.py       # RTSP, webcam, video file, and synthetic frame grabbers
│   ├── logger.py             # Vision subsystem logger
│   ├── main.py               # Standalone inference runner CLI
│   ├── tracker.py            # CentroidTracker with ID assignment & lifetime management
│   ├── tracking_types.py     # Track dataclasses, bounding boxes, trajectories
│   ├── types.py              # Detections, Points, Colors
│   └── visualizer.py         # OpenCV annotation renderer (bboxes, labels, tracks)
│
├── frontend/                 # React + TypeScript single-page application
│   ├── src/
│   │   ├── app/              # Router, providers, and layout wrappers
│   │   ├── components/       # UI component library
│   │   │   ├── detections/   # Detection lists, filter pills, inspector
│   │   │   ├── events/       # Event feeds, crossing logs, alerts
│   │   │   ├── layout/       # App header, sidebar, navigation
│   │   │   ├── regions/      # Canvas polygon and crossing line editors
│   │   │   ├── streams/      # Stream cards, grid views, stream control modal
│   │   │   ├── telemetry/    # Performance gauges, latency graphs, FPS charts
│   │   │   ├── ui/           # Buttons, inputs, badges, dialogs, modals
│   │   │   └── viewer/       # Canvas-based high-FPS stream viewer component
│   │   ├── config/           # API URLs, WebSocket endpoints, constants
│   │   ├── hooks/            # Custom React hooks (useWebSocketStream, useTelemetry)
│   │   ├── lib/              # Utility functions, formatting, classnames
│   │   ├── pages/            # Page components (DashboardPage, StreamsPage, StreamDetailPage)
│   │   ├── services/         # Axios / Fetch API client functions
│   │   ├── store/            # Zustand stores (useStreamStore, useTelemetryStore)
│   │   └── types/            # TypeScript interfaces for streams, detections, events
│   ├── package.json          # Frontend dependencies & scripts
│   ├── tailwind.config.ts    # Tailwind styling tokens
│   └── vite.config.ts        # Vite configuration & dev proxy
│
├── kubernetes/               # Helm / K8s manifests (Deployments, HPA, Services, PVC)
├── docker/                   # Supplementary Docker configs & entrypoints
├── docker-compose.yml        # Multi-service local composition (API, Frontend, Redis, Kafka, DB)
├── PROJECT_DOCS/             # Deep architectural docs, specs, and design references
├── docs/                     # Observability, Docker, and Compose documentation
├── tests/                    # Pytest test suite (unit, integration, load, auth)
├── alembic/                  # Database migration scripts
├── requirements.txt          # Python runtime requirements
└── requirements-dev.txt      # Python development & testing tools
```
