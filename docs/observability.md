# OmniTrack Observability

This document describes the observability subsystem: structured logging,
correlation IDs, Prometheus metrics, readiness/liveness probes, and error
reporting. It is the operational reference for running and monitoring the
platform before distributed infrastructure (Redis/Kafka/Postgres) is introduced.

---

## 1. Overview

The subsystem lives in `backend/observability/` and is wired into the FastAPI
app (`backend/main.py`) and the inference service (`backend/service.py`).

| Concern | Module | Surface |
|---|---|---|
| Structured JSON logging | `observability/logging.py` | stdout (JSON lines) |
| Correlation IDs | `observability/correlation.py` | `X-Request-ID` header, log fields |
| Metrics | `observability/metrics.py` | `GET /metrics` (Prometheus text) |
| Readiness / liveness | `observability/readiness.py` | `GET /ready`, `GET /live` |
| Error taxonomy | `observability/errors.py` | structured error logs |

Design guarantees: thread-safe, non-blocking on hot paths, low overhead, and
**no changes to any REST or WebSocket contract**.

---

## 2. Structured logging

Every log record is a single-line JSON object with a stable schema:

```json
{
  "timestamp": "2026-08-01T12:11:31.173Z",
  "level": "INFO",
  "logger": "backend.main",
  "component": "backend.api",
  "request_id": "9f2c1a...",
  "stream_id": "cam-01",
  "camera_id": "-",
  "event_id": "-",
  "thread": "MainThread:15096",
  "message": "Stream cam-01 started",
  "exception": null
}
```

Fields are always present; `-` means "not applicable in this context" and
`exception` is `null` unless a traceback was attached.

* **Non-blocking.** Records are formatted and written by a
  `logging.handlers.QueueListener` on a dedicated daemon thread. The calling
  thread only performs an unbounded `put_nowait`, so logging never blocks the
  inference loop or the ASGI loop.
* **Component names.** Loggers are created via
  `observability.logging.get_logger(name, component=...)` so records carry a
  stable, human-meaningful component (`backend.api`, `inference.stream`).
* **Extra context.** Attach queryable fields with
  `logger.info("msg", extra={"fields": {...}})`. Extras never clobber the
  guaranteed schema keys.
* **Uvicorn capture.** Uvicorn's own loggers propagate to the root logger and
  are emitted as JSON too.

### Configuration

* `OMNITRACK_LOGGING_LEVEL` — `DEBUG` | `INFO` | `WARNING` | `ERROR`.

---

## 3. Correlation IDs

A `request_id` ties together every log record produced while handling one unit
of work.

* **REST.** The HTTP middleware binds a request id per request. It honours an
  inbound `X-Request-ID` header (so callers/gateways can supply their own) and
  otherwise generates a UUID4 hex. The id is always echoed back on the
  `X-Request-ID` response header.
* **WebSockets.** Each connection binds `stream_id` at accept so all logs for
  that connection carry the stream.
* **Inference pipeline.** The inference thread binds `stream_id` once per loop,
  so every per-frame log is attributable to its stream. Derived events get an
  `event_id`.
* **Propagation.** Implemented with `contextvars.ContextVar`, which is both
  async-safe (per-task) and thread-safe (per-thread), so concurrent requests and
  background inference threads never leak ids into each other.

`camera_id` is propagated end-to-end as a **future-ready** field; it is `-`
until camera topology is introduced.

### Configuration

* `OMNITRACK_REQUEST_ID_HEADER` — header name for inbound/echoed id (default
  `X-Request-ID`).

---

## 4. Metrics

Metrics are exposed in Prometheus text format at `GET /metrics`. All names are
`omnitrack_<subsystem>_<thing>_<unit>`. Labels are kept low-cardinality;
`stream_id` is used only on per-stream series bounded by the small number of
live streams.

### API / HTTP

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `omnitrack_api_request_latency_seconds` | Histogram | `method`,`route`,`status_class` | Request latency; compute p50/p95/p99 in PromQL. `route` is the route template, not the raw path. |
| `omnitrack_api_requests_total` | Counter | `method`,`route`,`status_class` | Total requests. |
| `omnitrack_api_requests_in_flight` | Gauge | — | Requests currently being processed. |

### WebSockets

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `omnitrack_websocket_connections` | Gauge | `channel` (`frames`/`events`) | Currently open connections. |
| `omnitrack_websocket_connections_opened_total` | Counter | `channel` | Cumulative connections opened. |
| `omnitrack_websocket_messages_sent_total` | Counter | `channel`,`type` | Messages pushed to clients (`frame`,`keep_alive`,`event`,`gap`). |

### Streams

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `omnitrack_streams_active` | Gauge | — | Streams currently registered as active. |
| `omnitrack_streams_started_total` | Counter | — | Cumulative streams started. |
| `omnitrack_streams_stopped_total` | Counter | — | Cumulative streams stopped. |
| `omnitrack_stream_errors_total` | Counter | `stream_id` | Inference-loop errors. |

### Inference pipeline

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `omnitrack_inference_fps` | Gauge | `stream_id` | Effective throughput (frames/sec). |
| `omnitrack_model_inference_latency_seconds` | Histogram | `stream_id` | YOLO forward-pass latency. |
| `omnitrack_inference_frame_latency_seconds` | Histogram | `stream_id` | End-to-end per-frame pipeline latency (detect+track+render+enqueue). |
| `omnitrack_detection_latency_seconds` | Histogram | `stream_id` | Detection-stage latency. |
| `omnitrack_frames_processed_total` | Counter | `stream_id` | Frames processed. |
| `omnitrack_detections_total` | Counter | `stream_id` | Detections produced. |

### Queues / drops

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `omnitrack_frame_queue_depth` | Gauge | `stream_id` | Current output frame-queue depth. |
| `omnitrack_dropped_frames_total` | Counter | `stream_id` | Frames dropped (queue full). |
| `omnitrack_dropped_events_total` | Counter | `stream_id` | Live events dropped for slow WS consumers. |

### Events

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `omnitrack_events_total` | Counter | `stream_id`,`event_type`,`severity` | Derived events. Throughput = `rate(...)`. |

### Process / system

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `omnitrack_process_cpu_percent` | Gauge | — | Process CPU %. |
| `omnitrack_process_memory_bytes` | Gauge | — | Resident set size. |
| `omnitrack_process_threads` | Gauge | — | OS thread count. |
| `omnitrack_build_info` | Gauge | `version`,`environment` | Constant 1; build metadata. |

### Configuration

* `OMNITRACK_METRICS_ENABLED` — toggle the `/metrics` endpoint (default `true`).
* `OMNITRACK_METRICS_SYSTEM_SAMPLE_INTERVAL_SECONDS` — how often the background
  sampler refreshes CPU/memory/thread gauges (default `5.0`). Sampling runs off
  the request path on a daemon thread.

---

## 5. Probes: /health, /ready, /live

Three distinct probes separate "is the process up" from "can it serve traffic".

| Endpoint | Question | Fails when | Orchestrator action |
|---|---|---|---|
| `GET /live` | Is the process up and the event loop responsive? | Process wedged. Never touches dependencies. | Restart the container. |
| `GET /ready` | Can the service do useful work now? | Any dependency check fails. Returns `503` + per-check breakdown. | Withhold traffic until `200`. |
| `GET /health` | Legacy aggregate status + active-stream count. | — (kept for backward compatibility). | Informational. |

### Readiness checks (evaluated in order)

1. **configuration** — settings loaded and `sqlite_path` resolved.
2. **model** — the configured model file is present.
3. **stream_manager** — service constructed and not shut down.
4. **database** — a trivial `SELECT 1` round-trip succeeds.

A check that itself raises is reported as a failed check (never crashes the
probe). Example not-ready response (`503`):

```json
{
  "status": "not_ready",
  "ready": false,
  "checks": [
    {"name": "configuration", "ok": true, "detail": "valid"},
    {"name": "model", "ok": false, "detail": "model file missing: yolov8n.pt"}
  ]
}
```

---

## 6. Error reporting

A small, consistent exception taxonomy lives in `observability/errors.py`. Every
platform error carries a stable machine-readable `code` and optional structured
`context`.

```
OmniTrackError
├── ConfigurationError      (configuration_invalid)
├── ModelUnavailableError   (model_unavailable)
├── StreamError             (stream_error)
│   ├── StreamNotFoundError      (stream_not_found)
│   ├── StreamAlreadyExistsError (stream_already_exists)
│   ├── StreamNotRunningError    (stream_not_running)
│   └── StreamSourceError        (stream_source_error)
└── DatabaseError           (database_unavailable)
```

Rules:

* **Never swallow.** Contained exceptions (e.g. to keep the inference loop
  alive) are logged with full traceback at the point of containment via
  `errors.log_error(...)`, then re-raised or explicitly recorded.
* **Structured.** `log_error` emits a JSON record carrying `error_code`,
  `error_type`, message, traceback, and merged context, plus the correlation
  ids bound in the current context.

---

## 7. Alert thresholds (recommended starting points)

| Alert | PromQL | Threshold | Rationale |
|---|---|---|---|
| High API latency (p95) | `histogram_quantile(0.95, rate(omnitrack_api_request_latency_seconds_bucket[5m]))` | `> 0.5s` | User-facing API is slow. |
| API error rate | `rate(omnitrack_api_requests_total{status_class="5xx"}[5m])` | `> 0.05 req/s` | Server errors rising. |
| Stream errors | `rate(omnitrack_stream_errors_total[5m])` | `> 0` | Any inference-loop error warrants investigation. |
| Dropped frames | `rate(omnitrack_dropped_frames_total[5m])` | `> 1/s` | Consumers can't keep up; frames are being lost. |
| Dropped events | `rate(omnitrack_dropped_events_total[5m])` | `> 0` | Event consumers falling behind. |
| Frame queue saturation | `omnitrack_frame_queue_depth / on(stream_id) ... ` | `> 80%` of capacity | Back-pressure building. |
| Inference FPS collapse | `omnitrack_inference_fps` | `< expected` for the source | Pipeline degraded or source stalled. |
| High model latency (p95) | `histogram_quantile(0.95, rate(omnitrack_model_inference_latency_seconds_bucket[5m]))` | `> frame budget` | Model too slow for target FPS. |
| Memory growth | `omnitrack_process_memory_bytes` | sustained rise over 1h | Possible leak. |
| Not ready | `up` on `/ready` scrape, or probe status | `!= ready` for > 1m | Dependency down. |

Tune thresholds to your deployment; these are conservative starting points.

---

## 8. Grafana dashboard design (future)

A single "OmniTrack Overview" dashboard, plus a per-stream drill-down.

**Row 1 — Service health**
* Stat: `omnitrack_streams_active` (active streams).
* Stat: readiness status (from `/ready` via a probe or `up`).
* Gauge: `omnitrack_process_cpu_percent`, `omnitrack_process_memory_bytes`.
* Time series: `omnitrack_api_requests_in_flight`.

**Row 2 — API**
* Time series: request rate by `status_class` —
  `sum by (status_class)(rate(omnitrack_api_requests_total[5m]))`.
* Heatmap: `omnitrack_api_request_latency_seconds_bucket`.
* Stat: p95 latency.

**Row 3 — Real-time transport**
* Time series: `omnitrack_websocket_connections` by `channel`.
* Time series: message rate —
  `sum by (type)(rate(omnitrack_websocket_messages_sent_total[5m]))`.

**Row 4 — Inference (per stream, `repeat` by `stream_id`)**
* Time series: `omnitrack_inference_fps`.
* Heatmap: `omnitrack_model_inference_latency_seconds_bucket`.
* Time series: `omnitrack_frame_queue_depth`.
* Time series: `rate(omnitrack_dropped_frames_total[5m])`.

**Row 5 — Events**
* Time series: `sum by (event_type, severity)(rate(omnitrack_events_total[5m]))`.
* Time series: `rate(omnitrack_dropped_events_total[5m])`.

**Row 6 — Logs (Loki)**
* Log panel querying `{component=~"backend.*|inference.*"}`, filtered by
  `request_id` / `stream_id` for trace correlation.

---

## 9. Operational guide

* **Scrape config.** Point Prometheus at `http://<host>:<port>/metrics`. If
  `OMNITRACK_API_KEY` is set, present it via the `X-API-Key` header on the
  scrape (the endpoint passes through the API-key middleware).
* **Tracing a request.** Grep logs for the `request_id` echoed on the response
  `X-Request-ID` header.
* **Tracing a stream.** Filter logs by `stream_id`; correlate with the
  per-stream metrics of the same label.
* **Graceful shutdown.** On SIGINT/SIGTERM the app stops streams, disposes the
  DB engine, stops the system sampler, and flushes the logging queue so no
  buffered records are lost.
