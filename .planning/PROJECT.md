# Project: OmniTrack AI Platform

## Vision & Objective
OmniTrack is an enterprise-grade real-time AI video analytics and operational monitoring platform. It performs concurrent live object detection, multi-object tracking with persistent track IDs, spatial rule processing (dwell zones, crossing lines, loitering), telemetry monitoring, and low-latency visualization across 50+ concurrent HD camera streams.

## Key Stakeholders & Personas
- **Security Operations Centers (SOC)**: Rapid intrusion detection, real-time alert triage, and visual surveillance.
- **Facility & Logistics Managers**: Occupancy monitoring, vehicle/foot-traffic counting, and zone utilization analysis.
- **DevOps & ML Engineers**: Model deployment, inference latency optimization, distributed worker orchestration, and cluster telemetry.

## System Boundaries
- **Input Sources**: RTSP streams, MJPEG feeds, local webcams, pre-recorded video files, and synthetic test generators.
- **Processing Core**: Python 3.11+, YOLOv8 (PyTorch & ONNX Runtime), Centroid Tracking, NumPy zero-copy buffer pooling, dynamic batching.
- **Coordination & Event Streaming**: Redis (leases & caching), Confluent Kafka (high-throughput detection bus), PostgreSQL / SQLite (persistent events & audit trails).
- **Client Presentation**: React 18 + TypeScript + Vite, isolated Canvas rendering engine with `requestAnimationFrame`, Zustand state management, and Tailwind CSS.
- **Infrastructure**: Docker multi-container environment and Kubernetes manifests (HPA, StatefulSet/PVC, Prometheus metrics).

## Architecture Principles
1. **Decoupled Render Path**: React Virtual DOM is isolated from high-frequency video frames to prevent stutter and GC pressure.
2. **Bounded Memory Footprint**: Fixed-capacity frame pools drop stale frames under backpressure rather than ballooning memory.
3. **Stateless Scale-Out**: Worker processes are coordinate-driven via Redis leases and Kafka topics, enabling horizontal scaling on Kubernetes.
4. **Resilient Streaming**: Automatic reconnection, exponential backoff, and heartbeat health checks across WebSocket and RTSP connections.
