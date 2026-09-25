# Project Roadmap: OmniTrack AI Platform

## Milestone 1: Core Foundation & Tracking Pipeline (Status: Complete)
- [x] **Phase 01: Core Inference & Tracking** - YOLOv8 model loading, centroid tracking, zero-copy frame pool, and frame capture sources.
- [x] **Phase 02: API & Realtime Transport** - FastAPI backend, WebSocket frame streamer, and stream control REST endpoints.
- [x] **Phase 03: Canvas-Based Dashboard** - React + Vite dashboard, canvas render loop, Zustand telemetry store, and stream grid view.

## Milestone 2: Enterprise Distributed Architecture (Status: Complete)
- [x] **Phase 04: Distributed Coordination** - Redis stream lease management, leader election, and multi-worker failover.
- [x] **Phase 05: Event Bus & Persistence** - Confluent Kafka event integration, PostgreSQL detection store, and Alembic migrations.
- [x] **Phase 06: Spatial Intelligence & Alerting** - Polygonal intrusion zones, bidirectional crossing lines, and threshold alert dispatch.
- [x] **Phase 07: Observability & Production Monitoring** - Prometheus `/metrics`, Kubernetes liveness/readiness probes, and structured JSON logs.

## Milestone 3: Performance Optimization & Hardware Acceleration (Status: Current Target)
- [ ] **Phase 08: GPU & TensorRT Optimization** - CUDA/TensorRT engine compilation, dynamic batching tuning, and GPU memory management.
- [ ] **Phase 09: WebRTC Video Pipeline** - Evaluate WebRTC media stream transport to reduce client-side WebSocket bandwidth overhead.
- [ ] **Phase 10: Advanced Anomaly Detection** - Unsupervised behavioral anomaly detection (loitering, sudden crowd dispersion, wrong-way movement).

## Milestone 4: Multi-Tenant Enterprise Operations (Status: Planned)
- [ ] **Phase 11: Enterprise Auth & Audit** - SSO / OIDC integration, comprehensive security audit trails, and multi-tenant isolation.
- [ ] **Phase 12: Long-Term Storage & Video Archival** - S3 / MinIO cold storage integration with automated video retention policies.
