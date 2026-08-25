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
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

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

# ---------------------------------------------------------------------------
# Model manager (process-wide shared detector pool: refcounting + LRU eviction)
# ---------------------------------------------------------------------------

# Detector models currently resident in the manager pool (loaded, warm or in
# use). Bounded by the configured max-loaded cap except when every resident
# model is in use, in which case the pool is allowed to exceed the cap rather
# than evict a model a live stream still needs.
MODEL_LOADED_MODELS = Gauge(
    "omnitrack_model_loaded_models",
    "Detector models currently resident in the model-manager pool.",
    registry=registry,
)

# Acquisitions served by an already-resident shared model (no load).
MODEL_CACHE_HITS_TOTAL = Counter(
    "omnitrack_model_cache_hits_total",
    "Detector acquisitions served by an already-loaded shared model.",
    registry=registry,
)

# Acquisitions (or warm preloads) that had to load a new model.
MODEL_CACHE_MISSES_TOTAL = Counter(
    "omnitrack_model_cache_misses_total",
    "Detector acquisitions that required loading a new model.",
    registry=registry,
)

# Wall time to load a detector model into the pool (build + provider load +
# warmup), off the per-frame path (paid once per signature on a cache miss).
MODEL_LOAD_SECONDS = Histogram(
    "omnitrack_model_load_seconds",
    "Wall time to load a detector model into the manager pool.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
    registry=registry,
)

# Wall time to unload/evict a detector model (LRU eviction or shutdown).
MODEL_UNLOAD_SECONDS = Histogram(
    "omnitrack_model_unload_seconds",
    "Wall time to unload/evict a detector model from the manager pool.",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
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
# Frame pool (process-wide reusable image-buffer pool)
# ---------------------------------------------------------------------------

class _FramePoolCollector:
    """Bridge the dependency-free ``inference`` frame pool into Prometheus.

    The pool lives in the ``inference`` package and takes no observability
    dependency; this custom collector reads its counters at scrape time so the
    six frame-pool series are exposed through the same registry as everything
    else. When the pool is disabled the counters stay flat (all acquisitions
    miss and nothing is retained).
    """

    def collect(self):
        from inference.frame_pool import get_frame_pool

        stats = get_frame_pool().stats()
        yield GaugeMetricFamily(
            "omnitrack_frame_pool_size",
            "Reusable image buffers currently retained in the frame pool.",
            value=stats["size"],
        )
        yield GaugeMetricFamily(
            "omnitrack_buffer_reuse_ratio",
            "Fraction of buffer acquisitions served from the pool (0-1).",
            value=stats["reuse_ratio"],
        )
        yield CounterMetricFamily(
            "omnitrack_frame_pool_hits",
            "Buffer acquisitions served by a reused pooled buffer.",
            value=stats["hits"],
        )
        yield CounterMetricFamily(
            "omnitrack_frame_pool_misses",
            "Buffer acquisitions that allocated a new buffer.",
            value=stats["misses"],
        )
        yield CounterMetricFamily(
            "omnitrack_frame_allocations",
            "Image buffers allocated by the frame pool.",
            value=stats["allocations"],
        )
        yield CounterMetricFamily(
            "omnitrack_copy_operations",
            "Image-buffer copy operations performed via the frame pool.",
            value=stats["copies"],
        )


registry.register(_FramePoolCollector())


# ---------------------------------------------------------------------------
# Recording & Evidence Pipeline
# ---------------------------------------------------------------------------

RECORDINGS_STARTED_TOTAL = Counter(
    "omnitrack_recordings_started_total",
    "Total recording segments started, by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

RECORDINGS_STOPPED_TOTAL = Counter(
    "omnitrack_recordings_stopped_total",
    "Total recording segments stopped, by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

RECORDINGS_DELETED_TOTAL = Counter(
    "omnitrack_recordings_deleted_total",
    "Total recording segments deleted (retention/storage-max/manual).",
    registry=registry,
)

RECORDINGS_LISTED_TOTAL = Counter(
    "omnitrack_recordings_listed_total",
    "Total recordings returned by list endpoints.",
    registry=registry,
)

EVIDENCES_CREATED_TOTAL = Counter(
    "omnitrack_evidences_created_total",
    "Total evidence clips created, by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

ENCODING_FAILURES_TOTAL = Counter(
    "omnitrack_encoding_failures_total",
    "Total event-clip encoding failures, by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

DROPPED_RECORDING_FRAMES_TOTAL = Counter(
    "omnitrack_dropped_recording_frames_total",
    "Total frames dropped by the recording pipeline (queue full), by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

API_REQUEST_ERRORS_TOTAL = Counter(
    "omnitrack_api_request_errors_total",
    "Total internal errors in API handlers (recordings/evidences).",
    registry=registry,
)

RECORDING_FPS = Gauge(
    "omnitrack_recording_fps",
    "Effective recording throughput (frames/sec), by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

ENCODER_LATENCY = Histogram(
    "omnitrack_encoder_latency_seconds",
    "Event-clip encoder wall time in seconds, by stream.",
    labelnames=("stream_id",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
    registry=registry,
)

SEGMENT_COUNT = Gauge(
    "omnitrack_segment_count",
    "Current number of recording segments, by stream.",
    labelnames=("stream_id",),
    registry=registry,
)

STORAGE_USAGE_BYTES = Gauge(
    "omnitrack_storage_usage_bytes",
    "Current recording storage usage in bytes.",
    registry=registry,
)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

ANALYTICS_EVENTS_TOTAL = Counter(
    "omnitrack_analytics_events_total",
    "Total analytics events consumed, by stream and event type.",
    labelnames=("stream_id", "event_type"),
    registry=registry,
)

ANALYTICS_SUMMARIES_FLUSHED = Counter(
    "omnitrack_analytics_summaries_flushed_total",
    "Total analytics summary rows flushed to PostgreSQL.",
    registry=registry,
)

ANALYTICS_FLUSHES_TOTAL = Counter(
    "omnitrack_analytics_flushes_total",
    "Total analytics flush cycles completed.",
    registry=registry,
)

ANALYTICS_RETENTION_DELETES_TOTAL = Counter(
    "omnitrack_analytics_retention_deletes_total",
    "Total analytics summary rows deleted by retention policy.",
    registry=registry,
)

ANALYTICS_CACHE_HITS_TOTAL = Counter(
    "omnitrack_analytics_cache_hits_total",
    "Total analytics cache hits.",
    registry=registry,
)

ANALYTICS_CACHE_MISSES_TOTAL = Counter(
    "omnitrack_analytics_cache_misses_total",
    "Total analytics cache misses.",
    registry=registry,
)

ANALYTICS_QUERY_LATENCY = Histogram(
    "omnitrack_analytics_query_latency_seconds",
    "Latency of analytics API queries in seconds.",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
    registry=registry,
)

ANALYTICS_AGGREGATOR_STREAMS = Gauge(
    "omnitrack_analytics_aggregator_streams",
    "Number of streams currently tracked by the analytics aggregator.",
    registry=registry,
)

ANALYTICS_OBJECT_COUNT = Gauge(
    "omnitrack_analytics_object_count",
    "Current in-memory object count for a stream, by class.",
    labelnames=("stream_id", "class_name"),
    registry=registry,
)

ANALYTICS_ZONE_OCCUPANCY = Gauge(
    "omnitrack_analytics_zone_occupancy",
    "Current zone occupancy in seconds for a stream, by zone.",
    labelnames=("stream_id", "zone_name"),
    registry=registry,
)

ANALYTICS_LINE_CROSSINGS = Counter(
    "omnitrack_analytics_line_crossings_total",
    "Total line crossings by direction, by stream and line.",
    labelnames=("stream_id", "line_name", "direction"),
    registry=registry,
)


# ---------------------------------------------------------------------------
# Alert & Rule Engine
# ---------------------------------------------------------------------------

ALERT_RULES_EVALUATED_TOTAL = Counter(
    "omnitrack_alert_rules_evaluated_total",
    "Total alert rule evaluations performed.",
    registry=registry,
)

ALERTS_TRIGGERED_TOTAL = Counter(
    "omnitrack_alerts_triggered_total",
    "Total alert instances triggered.",
    registry=registry,
)

ALERTS_DEDUPLICATED_TOTAL = Counter(
    "omnitrack_alerts_deduplicated_total",
    "Total alert triggers suppressed by deduplication.",
    registry=registry,
)

ALERTS_ACKNOWLEDGED_TOTAL = Counter(
    "omnitrack_alerts_acknowledged_total",
    "Total alerts acknowledged.",
    registry=registry,
)

ALERTS_RESOLVED_TOTAL = Counter(
    "omnitrack_alerts_resolved_total",
    "Total alerts resolved.",
    registry=registry,
)

ALERT_NOTIFICATIONS_SUCCESS_TOTAL = Counter(
    "omnitrack_alert_notifications_success_total",
    "Total successful alert notification deliveries.",
    registry=registry,
)

ALERT_NOTIFICATIONS_FAILURE_TOTAL = Counter(
    "omnitrack_alert_notifications_failure_total",
    "Total failed alert notification deliveries (after retries).",
    registry=registry,
)

ALERT_RULE_EVALUATION_LATENCY = Histogram(
    "omnitrack_alert_rule_evaluation_latency_seconds",
    "Latency of a single alert rule evaluation in seconds.",
    buckets=(0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5),
    registry=registry,
)

WS_DISCONNECTS_TOTAL = Counter(
    "omnitrack_websocket_disconnects_total",
    "Total WebSocket disconnections, by channel.",
    labelnames=("channel",),
    registry=registry,
)

STREAM_LIFETIME_SECONDS = Histogram(
    "omnitrack_stream_lifetime_seconds",
    "Wall time a stream was active before stopping, in seconds.",
    buckets=(5, 15, 30, 60, 120, 300, 600, 1800, 3600),
    registry=registry,
)

WORKER_HEALTH = Gauge(
    "omnitrack_worker_health",
    "Background worker liveness: 1=running, 0=down.",
    labelnames=("worker",),
    registry=registry,
)

WORKER_FAILURES_TOTAL = Counter(
    "omnitrack_worker_failures_total",
    "Total uncaught background worker thread failures.",
    labelnames=("worker",),
    registry=registry,
)

WORKER_RESTARTS_TOTAL = Counter(
    "omnitrack_worker_restarts_total",
    "Total background worker restarts attempted by the supervisor.",
    labelnames=("worker",),
    registry=registry,
)

WORKER_QUEUE_DEPTH = Gauge(
    "omnitrack_worker_queue_depth",
    "Current input-queue depth for a background worker.",
    labelnames=("worker",),
    registry=registry,
)

DEPENDENCY_HEALTH = Gauge(
    "omnitrack_dependency_health",
    "Dependency reachability: 1=healthy, 0.5=degraded, 0=unhealthy.",
    labelnames=("dependency",),
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
