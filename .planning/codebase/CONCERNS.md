# Known Concerns, Tech Debt & Risk Assessment

## Technical Debt & Performance Considerations

### 1. GPU Acceleration & Cross-Platform Execution
- **Current State**: The inference engine defaults to CPU execution with PyTorch / ONNX Runtime. When scaled to 50+ concurrent 1080p RTSP streams, CPU inference will saturate and introduce latency spikes.
- **Remediation**: Configure TensorRT runtime bindings with CUDA execution providers in production Docker images (`Dockerfile.backend`) and enable GPU passthrough in Kubernetes deployment manifests.

### 2. Video Codec & Frame Compression Overhead
- **Current State**: Frames are encoded into JPEG and sent over WebSockets via Base64 or raw binary strings.
- **Concern**: High network bandwidth consumption per stream (approx. 2-5 Mbps per active UI viewer at 30 FPS).
- **Remediation**: Consider introducing WebRTC or HLS/fMP4 video pipelines for client-side streaming, retaining WebSockets primarily for detection coordinates and telemetry metadata.

### 3. Database Write Throughput for High Detection Volumes
- **Current State**: Detections and events are committed via SQLAlchemy ORM.
- **Concern**: In dense scenes (100+ objects per frame across multiple streams), direct database inserts can become a write bottleneck.
- **Remediation**: Ensure the Kafka event pipeline (`confluent-kafka`) handles buffering with batch ingestion into timeseries storage or PostgreSQL partitioned tables via the background writer.

### 4. Distributed Stream Failover Recovery Window
- **Current State**: Redis leases expire after a configurable TTL (e.g. 5-10 seconds) before an alternate replica claims a dropped stream.
- **Concern**: Temporary blind spots (up to lease TTL) during worker node failure before lease transition completes.
- **Remediation**: Optimize heartbeat intervals and add graceful pre-emption during Kubernetes pod eviction signals (`preStop` hooks).

## Security & Operational Risk

### 1. Default Secrets & Configuration Hardening
- Secret keys and JWT secrets in `.env.example` must be replaced with strong, cryptographically secure secrets in staging and production.
- Production readiness config validator (`validate_production_config`) should be strictly enforced at startup.

### 2. RTSP Authentication & Network Isolation
- External camera streams may traverse untrusted local networks. TLS/SRTP or VPN tunneling should be utilized when connecting remote edge devices.
