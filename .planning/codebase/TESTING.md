# Testing Strategy & Guidelines

## Backend Testing (Pytest)

### Test Suite Structure
The `tests/` directory contains automated test suites testing isolation, resilience, and system invariants:

- `test_alerts.py`: Alert rule evaluation, event thresholds, notification dispatches.
- `test_analytics.py`: Aggregate statistics calculations, Polars metric queries.
- `test_auth.py`: JWT token generation, role permission verification, expired token rejection.
- `test_batching.py`: Multi-stream dynamic frame batching queues and latency limits.
- `test_ci_pipeline.py`: CI configuration, code quality, and environment invariants.
- `test_compose_topology.py`: Docker Compose port mappings, service names, and networking.
- `test_distributed_scaling.py`: Horizontal scaling simulation, Redis lease coordination.
- `test_error_envelope.py`: Standardized HTTP error format consistency.
- `test_frame_pool.py`: Zero-copy frame memory pool allocation, reuse, and release.
- `test_model_manager.py`: Model loading, hot-swapping between YOLO and ONNX formats.
- `test_observability.py`: Prometheus metrics generation, counter increments, health probes.
- `test_providers.py`: Video frame provider abstractions and reconnect resilience.
- `test_recording.py`: Video recording segmentation, disk writing, and retention cleanup.
- `test_scheduler.py`: Dynamic stream scheduling and worker assignment.
- `test_settings.py`: Configuration validation, environment variable parsing defaults.
- `test_shutdown_hardening.py`: Clean thread termination, resource disposal during SIGINT/SIGTERM.
- `test_stream_lifecycle.py`: Stream state transitions (STOPPED -> STARTING -> RUNNING -> STOPPED).
- `tests/perf/`: Load testing and latency benchmarking scripts.

### Running Backend Tests
```bash
# Run full test suite
pytest -v

# Run with coverage report
pytest --cov=backend --cov=inference tests/

# Run a specific module
pytest tests/test_stream_lifecycle.py
```

## Frontend Testing & Verification

### Static Analysis & Type Checking
- TypeScript compiler verification:
  ```bash
  cd frontend
  npm run typecheck
  ```
- ESLint checks:
  ```bash
  cd frontend
  npm run lint
  ```
- Production build validation:
  ```bash
  cd frontend
  npm run build
  ```
