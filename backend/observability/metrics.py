"""
Prometheus metrics for the OmniTrack platform.

A single, process-wide registry of every metric the platform exposes. All
instruments are defined here once (at import) and imported wherever they are
recorded, so metric names/labels/HELP text live in exactly one place.

Conventions
-----------
* Names are ``omnitrack_<subsystem>_<thing>_<unit>`` per Prometheus best
  practice (https://prometheus.io/docs/practices/naming/).
* Labels are kept low-cardinality. ``stream_id`` is used only on per-stream
  gauges/counters whose series are bounded by the (small) number of live
  streams; it is never used on high-frequency request counters.
* All instruments are thread-safe (prometheus_client handles atomicity), so
  the inference thread and the ASGI loop can record concurrently.

The module is import-safe and side-effect-light: defining a metric allocates
the instrument but performs no I/O. The ``/metrics`` exposition endpoint and
the background system-sampler are wired up separately (see ``main.py`` and
``readiness.py``).
"""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    REGISTRY,
    generate_latest,
)

# Use the default global registry so the exposition endpoint and any future
# custom collectors share one view of the world.
registry: CollectorRegistry = REGISTRY

# ---------------------------------------------------------------------------
# API / HTTP
# ---------------------------------------------------------------------------

# Request latency, partitioned by route template + method + status class.
# Histogram so we can compute p50/p95/p99 in PromQL.
API_REQUEST_LATENCY = Histogram(
    "omnitrack_api_request_latency_seconds",
    "HTTP request latency in seconds, by route, method and status class.",
    labelnames=("method", "route", "status_class"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry,
)

# Total requests, same partition as latency (counters are cheap).
API_REQUESTS_TOTAL = Counter(
    "omnitrack_api_requests_total",
    "Total HTTP requests, by route, method and status class.",
    labelnames=("method", "route", "status_class"),
    registry=registry,
)

# Requests currently being handled (concurrency gauge).
API_REQUESTS_IN_FLIGHT = Gauge(
    "omnitrack_api_requests_in_flight",
    "HTTP requests currently being processed.",
    registry=registry,
)

# ---------------------------------------------------------------------------
# WebSockets
# ---------------------------------------------------------------------------

# Live WebSocket connections, split by channel (frames vs events).
WS_CONNECTIONS = Gauge(
    "omnitrack_websocket_connections",
    "Currently open WebSocket connections, by channel.",
    labelnames=("channel",),  # "frames" | "events"
    registry=registry,
)

# Cumulative connections opened, by channel. Named ``..._opened_total`` so its
# base name does not collide with the ``omnitrack_websocket_connections`` gauge
# (a Counter strips its ``_total`` suffix when registering).
WS_CONNECTIONS_TOTAL = Counter(
    "omnitrack_websocket_connections_opened_total",
    "Total WebSocket connections opened, by channel.",
    labelnames=("channel",),
    registry=registry,
)

# Messages sent to clients, by channel and message type.
WS_MESSAGES_SENT_TOTAL = Counter(
    "omnitrack_websocket_messages_sent_total",
    "Total WebSocket messages sent to clients, by channel and type.",
    labelnames=("channel", "type"),
    registry=registry,
)

# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------

ACTIVE_STREAMS = Gauge(
    "omnitrack_streams_active",
    "Number of inference streams currently registered as active.",
    registry=registry,
)

STREAMS_STARTED_TOTAL = Counter(
    "omnitrack_streams_started_total",
    "Total inference streams started.",
    registry=registry,
)

STREAMS_STOPPED_TOTAL = Counter(
    "omnitrack_streams_stopped_total",
    "Total inference streams stopped.",
    registry=registry,
)

STREAM_ERRORS_TOTAL = Counter(
    "omnitrack_stream_errors_total",
    "Total inference-loop errors, by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

# ---------------------------------------------------------------------------
# Inference pipeline (per stream)
# ---------------------------------------------------------------------------

INFERENCE_FPS = Gauge(
    "omnitrack_inference_fps",
    "Effective inference throughput (frames/sec), by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

# Which inference backend is active, as an info-style gauge fixed at 1 with a
# ``provider`` label ("torch", "onnx", "openvino", "tensorrt"). Set once at startup.
INFERENCE_PROVIDER = Gauge(
    "omnitrack_inference_provider",
    "Active inference provider (torch|onnx|openvino|tensorrt); fixed at 1 with a provider label.",
    labelnames=("provider",),
    registry=registry,
)

# Wall time spent loading the inference provider (model load/export/compile),
# fixed at 1 with a provider label so the active provider's load cost is
# queryable without label churn. Set once at startup.
INFERENCE_PROVIDER_LOAD_TIME = Gauge(
    "omnitrack_inference_provider_load_time_seconds",
    "Inference provider model load time in seconds; fixed at 1 with a provider label.",
    labelnames=("provider",),
    registry=registry,
)

# Wall time of the one-time provider benchmark sweep (auto mode only). 0 when
# benchmarking is disabled or served from cache. Set once at startup.
INFERENCE_BENCHMARK_DURATION = Gauge(
    "omnitrack_inference_benchmark_duration_seconds",
    "Duration of the one-time provider benchmark sweep in seconds.",
    registry=registry,
)

# Benchmark score (throughput, FPS) of each candidate provider, labelled by
# provider. Higher is better; the highest-scoring provider is selected.
INFERENCE_PROVIDER_SCORE = Gauge(
    "omnitrack_inference_provider_score",
    "Benchmark score (throughput FPS) per provider from the selection sweep.",
    labelnames=("provider",),
    registry=registry,
)

# ---------------------------------------------------------------------------
# Scheduler (optional central worker pool)
# ---------------------------------------------------------------------------

# Per-stream input-channel depth in the scheduler. Bounded by the configured
# stream queue capacity; the scheduler drops the oldest frame when a channel is
# full (counted in DROPPED_FRAMES_TOTAL, reused — no separate drop metric).
SCHEDULER_QUEUE_DEPTH = Gauge(
    "omnitrack_scheduler_queue_depth",
    "Current per-stream input-queue depth in the inference scheduler.",
    labelnames=("stream_id",),
    registry=registry,
)

# Fraction of the scheduler worker pool currently busy (busy / total workers).
SCHEDULER_WORKER_UTILIZATION = Gauge(
    "omnitrack_scheduler_worker_utilization",
    "Fraction of inference-scheduler workers currently busy (0-1).",
    registry=registry,
)

# End-to-end scheduler latency: frame submit -> processing completion.
SCHEDULER_STREAM_LATENCY = Histogram(
    "omnitrack_scheduler_stream_latency_seconds",
    "Time from frame submit to processing completion in the scheduler, by stream.",
    labelnames=("stream_id",),
    buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0),
    registry=registry,
)

# Queue-wait latency: frame submit -> worker pickup (scheduling delay only).
SCHEDULER_LATENCY = Histogram(
    "omnitrack_scheduler_latency_seconds",
    "Time a frame waits in the scheduler queue before worker pickup, by stream.",
    labelnames=("stream_id",),
    buckets=(0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5),
    registry=registry,
)

# Model forward-pass latency distribution.
MODEL_INFERENCE_LATENCY = Histogram(
    "omnitrack_model_inference_latency_seconds",
    "YOLO model forward-pass latency in seconds, by stream.",
    labelnames=("stream_id",),
    buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0),
    registry=registry,
)

# End-to-end per-frame pipeline latency (detect + track + render + enqueue).
INFERENCE_FRAME_LATENCY = Histogram(
    "omnitrack_inference_frame_latency_seconds",
    "End-to-end per-frame pipeline latency in seconds, by stream.",
    labelnames=("stream_id",),
    buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0),
    registry=registry,
)

# ---------------------------------------------------------------------------
# Dynamic batching (optional shared-detector forward-pass fusion)
# ---------------------------------------------------------------------------

# Frames per fused forward pass. Distribution centers on the achieved batch
# size; the low end reflects the single-frame fast path and partial batches.
BATCH_SIZE = Histogram(
    "omnitrack_batch_size",
    "Frames per fused inference forward pass.",
    buckets=(1, 2, 3, 4, 6, 8, 12, 16, 24, 32),
    registry=registry,
)

# Wall time of a fused batch forward pass, including per-image preprocessing
# and postprocessing.
BATCH_LATENCY = Histogram(
    "omnitrack_batch_latency_seconds",
    "Fused batch forward-pass wall time in seconds.",
    buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0),
    registry=registry,
)

# Achieved batch size as a fraction of the configured maximum (0-1). Reflects
# how well concurrent frame availability fills each batch.
BATCHING_EFFICIENCY = Gauge(
    "omnitrack_batching_efficiency",
    "Last fused batch size as a fraction of the configured maximum (0-1).",
    registry=registry,
)

# Per-stage latency within the inference pipeline. The ``stage`` label names
# the pipeline stage (capture/preprocess/inference/tracking/event/render/
# serialize) so a slow stage can be isolated from the end-to-end frame latency.
PIPELINE_STAGE_LATENCY = Histogram(
    "omnitrack_pipeline_stage_latency_seconds",
    "Latency of an individual inference pipeline stage",
    labelnames=("stream_id", "stage"),
    buckets=(0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0),
    registry=registry,
)

# Detection latency == model latency for the detection stage specifically.
DETECTION_LATENCY = Histogram(
    "omnitrack_detection_latency_seconds",
    "Detection-stage latency in seconds, by stream.",
    labelnames=("stream_id",),
    buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0),
    registry=registry,
)

FRAMES_PROCESSED_TOTAL = Counter(
    "omnitrack_frames_processed_total",
    "Total frames processed, by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

DETECTIONS_TOTAL = Counter(
    "omnitrack_detections_total",
    "Total detections produced, by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

# ---------------------------------------------------------------------------
# Queues / drops
# ---------------------------------------------------------------------------

FRAME_QUEUE_DEPTH = Gauge(
    "omnitrack_frame_queue_depth",
    "Current output frame-queue depth, by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

DROPPED_FRAMES_TOTAL = Counter(
    "omnitrack_dropped_frames_total",
    "Total frames dropped because the output queue was full, by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

DROPPED_EVENTS_TOTAL = Counter(
    "omnitrack_dropped_events_total",
    "Total live events dropped for slow WebSocket consumers, by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

EVENTS_TOTAL = Counter(
    "omnitrack_events_total",
    "Total derived events, by stream, type and severity.",
    labelnames=("stream_id", "event_type", "severity"),
    registry=registry,
)

# Event throughput is derived in PromQL as rate(omnitrack_events_total[...]);
# no separate gauge is needed.

# ---------------------------------------------------------------------------
# Process / system
# ---------------------------------------------------------------------------

PROCESS_CPU_PERCENT = Gauge(
    "omnitrack_process_cpu_percent",
    "Process CPU utilization percent (0-100 * ncores possible).",
    registry=registry,
)

PROCESS_MEMORY_BYTES = Gauge(
    "omnitrack_process_memory_bytes",
    "Process resident set size in bytes.",
    registry=registry,
)

PROCESS_THREADS = Gauge(
    "omnitrack_process_threads",
    "Number of OS threads in the process.",
    registry=registry,
)

# Build info, exposed as a labelled gauge with constant value 1.
BUILD_INFO = Gauge(
    "omnitrack_build_info",
    "Build and version metadata (constant 1).",
    labelnames=("version", "environment"),
    registry=registry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def observe_pipeline_stage(stream_id: str, stage: str, duration_ms: float) -> None:
    """Record one pipeline stage's latency.

    Cheap, single-metric helper so the hot loop can time individual stages
    (capture/preprocess/inference/tracking/event/render/serialize) without
    repeating label/second-conversion boilerplate.
    """
    PIPELINE_STAGE_LATENCY.labels(stream_id=stream_id, stage=stage).observe(
        max(duration_ms, 0.0) / 1000.0
    )


def observe_inference(
    stream_id: str,
    inference_time_ms: float,
    frame_latency_ms: float,
    num_detections: int,
) -> None:
    """Record one inference-loop iteration's metrics in a single call.

    Centralizing the per-frame recording keeps the hot loop in
    ``service.py`` to one cheap function call and guarantees the related
    instruments are always updated together.
    """
    inf_s = max(inference_time_ms, 0.0) / 1000.0
    frame_s = max(frame_latency_ms, 0.0) / 1000.0
    MODEL_INFERENCE_LATENCY.labels(stream_id=stream_id).observe(inf_s)
    DETECTION_LATENCY.labels(stream_id=stream_id).observe(inf_s)
    INFERENCE_FRAME_LATENCY.labels(stream_id=stream_id).observe(frame_s)
    FRAMES_PROCESSED_TOTAL.labels(stream_id=stream_id).inc()
    if num_detections:
        DETECTIONS_TOTAL.labels(stream_id=stream_id).inc(num_detections)
    if frame_s > 0:
        INFERENCE_FPS.labels(stream_id=stream_id).set(1.0 / frame_s)


def render_metrics() -> bytes:
    """Return the Prometheus text exposition for the whole registry."""
    return generate_latest(registry)


def status_class(status_code: int) -> str:
    """Collapse an HTTP status code to its class label (``2xx``/``4xx``/...)."""
    return f"{status_code // 100}xx"
