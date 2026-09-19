"""Performance measurement harness for OmniTrack (single and multi-stream).

This module wires up ``InferenceStream``s with synthetic components
(``SyntheticFrameSource`` + ``MockDetector``) and measures delivered FPS,
per-frame latency, memory, and frame-drop behaviour over a configurable
duration.

It does **not** touch production code: the synthetic components are
injected by monkeypatching ``_initialize_components`` on the stream
instance.

Usage (standalone single)::
    python -m tests.perf.perf_runner --duration 30 --detector-latency-ms 10

Usage (standalone multi)::
    python -m tests.perf.perf_runner --num-streams 4 --duration 30
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

import psutil

from inference.config import VisualizerConfig
from inference.tracker import CentroidTracker
from inference.tracking_config import TrackingConfig
from inference.visualizer import Visualizer
from inference.events import EventEngine, EventEngineConfig

from backend.service import InferenceStream, StreamConfig, InferenceService

from .synthetic_source import SyntheticFrameSource
from .mock_detector import MockDetector


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class PerfResult:
    """Collected performance measurements from a single stream."""
    duration_seconds: float = 0.0
    total_frames_produced: int = 0
    total_frames_consumed: int = 0

    delivered_fps: float = 0.0
    capture_fps: float = 0.0

    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    latency_mean_ms: float = 0.0

    peak_rss_mb: float = 0.0
    thread_count: int = 0

    frames_dropped: int = 0
    drop_rate_pct: float = 0.0

    detector_latency_ms: float = 0.0
    frame_width: int = 0
    frame_height: int = 0
    fps_cap: float = 0.0

    errors: list[str] = field(default_factory=list)


@dataclass
class MultiPerfResult:
    """Aggregated performance measurements for multiple streams."""
    num_streams: int = 0
    duration_seconds: float = 0.0
    
    # Aggregated throughput (sum of all streams)
    aggregate_delivered_fps: float = 0.0
    aggregate_capture_fps: float = 0.0
    
    # Global Latency percentiles (calculated across ALL frames from all streams)
    global_latency_p50_ms: float = 0.0
    global_latency_p95_ms: float = 0.0
    global_latency_p99_ms: float = 0.0
    global_latency_mean_ms: float = 0.0

    peak_rss_mb: float = 0.0
    thread_count: int = 0
    
    total_frames_produced: int = 0
    total_frames_consumed: int = 0
    total_frames_dropped: int = 0
    aggregate_drop_rate_pct: float = 0.0
    
    # Fairness / variance
    fps_variance: float = 0.0

    # Per stream results
    stream_results: list[PerfResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _patch_stream(
    stream: InferenceStream,
    source: SyntheticFrameSource,
    detector: MockDetector,
) -> None:
    original_init = stream._initialize_components

    def _synthetic_init() -> None:
        stream.frame_source = source
        source.open()
        stream.detector = detector

        tracking_config = TrackingConfig(
            enabled=stream.config.tracking_enabled,
            centroid_distance_threshold=stream.config.track_distance,
            max_age=stream.config.max_age,
        )
        stream.tracker = CentroidTracker(tracking_config)

        stream.event_engine = EventEngine(
            stream_id=stream.config.stream_id,
            config=EventEngineConfig(),
        )

        stream.visualizer = Visualizer(VisualizerConfig(
            show_fps=True,
            show_confidence=True,
            show_trajectories=True,
        ))

    stream._initialize_components = _synthetic_init


def _drain_stream(
    stream: InferenceStream, 
    source: SyntheticFrameSource,
    duration_seconds: float, 
    start_time: float, 
    result_out: PerfResult, 
    latencies_out: list[float]
) -> None:
    """Drains a stream's output queue in a background thread."""
    consumed = 0
    deadline = start_time + duration_seconds
    try:
        while time.perf_counter() < deadline:
            item = stream.get_output_frame(timeout=0.1)
            if item is not None:
                consumed += 1
                latencies_out.append((time.time() - item["timestamp"]) * 1000.0)
    except Exception as exc:
        result_out.errors.append(f"consumer error: {exc}")
    
    result_out.total_frames_consumed = consumed


def run_single_stream(
    duration_seconds: float = 30.0,
    detector_latency_ms: float = 10.0,
    num_detections: int = 5,
    width: int = 640,
    height: int = 480,
    fps_cap: float = 0.0,
    tracking_enabled: bool = True,
    db_path: Optional[str] = None,
    scheduler_enabled: bool = False,
    scheduler_workers: int = 2,
) -> PerfResult:
    """Run a single synthetic stream and collect performance data."""
    multi_res = run_multi_stream(
        num_streams=1,
        duration_seconds=duration_seconds,
        detector_latency_ms=detector_latency_ms,
        num_detections=num_detections,
        width=width,
        height=height,
        fps_cap=fps_cap,
        tracking_enabled=tracking_enabled,
        db_path=db_path,
        scheduler_enabled=scheduler_enabled,
        scheduler_workers=scheduler_workers,
    )
    if multi_res.stream_results:
        res = multi_res.stream_results[0]
        res.peak_rss_mb = multi_res.peak_rss_mb
        res.thread_count = multi_res.thread_count
        res.errors.extend(multi_res.errors)
        return res
    return PerfResult(errors=["No stream result returned"])


def run_multi_stream(
    num_streams: int = 1,
    duration_seconds: float = 30.0,
    detector_latency_ms: float = 10.0,
    num_detections: int = 5,
    width: int = 640,
    height: int = 480,
    fps_cap: float = 0.0,
    tracking_enabled: bool = True,
    db_path: Optional[str] = None,
    scheduler_enabled: bool = False,
    scheduler_workers: int = 2,
) -> MultiPerfResult:
    """Run N synthetic streams concurrently and collect aggregate data."""
    global_result = MultiPerfResult(
        num_streams=num_streams,
    )
    process = psutil.Process()

    if db_path is None:
        fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="omni_perf_")
        import os
        os.close(fd)
        actual_db = f"sqlite:///{tmp_path}"
    else:
        actual_db = db_path
        tmp_path = None

    service = InferenceService(
        db_path=actual_db,
        scheduler_enabled=scheduler_enabled,
        scheduler_workers=scheduler_workers,
    )

    streams = []
    sources = []
    detectors = []
    stream_results = []
    
    # 1. Build and patch all streams
    for i in range(num_streams):
        source = SyntheticFrameSource(
            width=width,
            height=height,
            fps_cap=fps_cap,
            total_frames=None,
        )
        detector = MockDetector(
            latency_ms=detector_latency_ms,
            num_detections=num_detections,
            image_size=(width, height),
        )
        config = StreamConfig(
            stream_id=f"perf-bench-{i}",
            source=f"synthetic://perf-{i}",
            width=width,
            height=height,
            fps=30,
            tracking_enabled=tracking_enabled,
            model_path="mock",
            inference_device="cpu",
            inference_provider="mock",
            frame_queue_capacity=60,
            adaptive_frame_drop=True,
        )
        stream = InferenceStream(
            config,
            service.Session,
            event_buffer=service.event_store.get_or_create(config.stream_id),
            scheduler=service._get_scheduler(),
        )
        _patch_stream(stream, source, detector)
        
        streams.append(stream)
        sources.append(source)
        detectors.append(detector)
        
        stream_results.append(PerfResult(
            detector_latency_ms=detector_latency_ms,
            frame_width=width,
            frame_height=height,
            fps_cap=fps_cap,
        ))

    # 2. Start all streams
    t_start = time.perf_counter()
    for i, stream in enumerate(streams):
        try:
            stream.start()
        except Exception as exc:
            global_result.errors.append(f"start failed for stream {i}: {exc}")

    # 3. Spawn consumer threads
    consumer_threads = []
    all_latencies: list[list[float]] = [[] for _ in range(num_streams)]
    
    for i in range(num_streams):
        t = threading.Thread(
            target=_drain_stream,
            args=(streams[i], sources[i], duration_seconds, t_start, stream_results[i], all_latencies[i]),
            daemon=True
        )
        consumer_threads.append(t)
        t.start()

    # 4. Wait for consumers to finish (they respect the duration)
    for t in consumer_threads:
        t.join(timeout=duration_seconds + 5)

    t_elapsed = time.perf_counter() - t_start

    # 5. Stop all streams
    for i, stream in enumerate(streams):
        try:
            stream.stop()
        except Exception as exc:
            global_result.errors.append(f"stop error for stream {i}: {exc}")

    peak_rss = process.memory_info().rss
    global_result.peak_rss_mb = round(peak_rss / (1024 * 1024), 2)
    global_result.thread_count = threading.active_count()
    global_result.duration_seconds = round(t_elapsed, 3)

    # 6. Aggregate results
    global_latencies = []
    stream_fps_list = []
    
    for i in range(num_streams):
        res = stream_results[i]
        src = sources[i]
        
        res.duration_seconds = round(t_elapsed, 3)
        res.total_frames_produced = src.frames_produced
        res.delivered_fps = round(res.total_frames_consumed / max(t_elapsed, 0.001), 2)
        res.capture_fps = round(src.frames_produced / max(t_elapsed, 0.001), 2)
        
        stream_fps_list.append(res.delivered_fps)
        
        res.frames_dropped = max(src.frames_produced - res.total_frames_consumed, 0)
        if src.frames_produced > 0:
            res.drop_rate_pct = round(100.0 * res.frames_dropped / src.frames_produced, 2)
            
        stream_latencies = all_latencies[i]
        if stream_latencies:
            stream_latencies.sort()
            res.latency_mean_ms = round(statistics.mean(stream_latencies), 3)
            res.latency_p50_ms = round(stream_latencies[int(len(stream_latencies) * 0.50)], 3)
            res.latency_p95_ms = round(stream_latencies[int(len(stream_latencies) * 0.95)], 3)
            res.latency_p99_ms = round(stream_latencies[int(len(stream_latencies) * 0.99)], 3)
            global_latencies.extend(stream_latencies)
            
        global_result.total_frames_produced += res.total_frames_produced
        global_result.total_frames_consumed += res.total_frames_consumed
        global_result.total_frames_dropped += res.frames_dropped
        global_result.aggregate_delivered_fps += res.delivered_fps
        global_result.aggregate_capture_fps += res.capture_fps

    if global_result.total_frames_produced > 0:
        global_result.aggregate_drop_rate_pct = round(
            100.0 * global_result.total_frames_dropped / global_result.total_frames_produced, 2
        )
        
    if len(stream_fps_list) > 1:
        global_result.fps_variance = round(statistics.variance(stream_fps_list), 2)

    if global_latencies:
        global_latencies.sort()
        global_result.global_latency_mean_ms = round(statistics.mean(global_latencies), 3)
        global_result.global_latency_p50_ms = round(global_latencies[int(len(global_latencies) * 0.50)], 3)
        global_result.global_latency_p95_ms = round(global_latencies[int(len(global_latencies) * 0.95)], 3)
        global_result.global_latency_p99_ms = round(global_latencies[int(len(global_latencies) * 0.99)], 3)

    global_result.stream_results = stream_results

    try:
        service.engine.dispose()
        if tmp_path is not None:
            import os
            os.remove(tmp_path)
    except Exception:
        pass

    return global_result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OmniTrack performance baseline runner"
    )
    parser.add_argument(
        "--num-streams", type=int, default=1,
        help="Number of concurrent streams (default: 1)",
    )
    parser.add_argument(
        "--duration", type=float, default=30.0,
        help="Run duration in seconds (default: 30)",
    )
    parser.add_argument(
        "--detector-latency-ms", type=float, default=10.0,
        help="Simulated per-frame inference latency in ms (default: 10)",
    )
    parser.add_argument(
        "--num-detections", type=int, default=5,
        help="Synthetic detections per frame (default: 5)",
    )
    parser.add_argument(
        "--width", type=int, default=640,
        help="Frame width (default: 640)",
    )
    parser.add_argument(
        "--height", type=int, default=480,
        help="Frame height (default: 480)",
    )
    parser.add_argument(
        "--fps-cap", type=float, default=0.0,
        help="Source FPS cap; 0=uncapped (default: 0)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON to stdout",
    )
    parser.add_argument(
        "--scheduler-enabled", action="store_true",
        help="Enable the central InferenceScheduler",
    )
    parser.add_argument(
        "--scheduler-workers", type=int, default=4,
        help="Number of shared workers if scheduler is enabled (default: 4)",
    )
    args = parser.parse_args()

    if args.num_streams == 1:
        result = run_single_stream(
            duration_seconds=args.duration,
            detector_latency_ms=args.detector_latency_ms,
            num_detections=args.num_detections,
            width=args.width,
            height=args.height,
            fps_cap=args.fps_cap,
            scheduler_enabled=args.scheduler_enabled,
            scheduler_workers=args.scheduler_workers,
        )
        if args.json:
            print(json.dumps(asdict(result), indent=2))
        else:
            print("\n" + "=" * 60)
            print(" OmniTrack Single-Stream Performance Baseline")
            print("=" * 60)
            print(f"  Duration .............. {result.duration_seconds:.1f} s")
            print(f"  Delivered FPS ......... {result.delivered_fps}")
            print(f"  Capture FPS ........... {result.capture_fps}")
            print(f"  Latency p50 ........... {result.latency_p50_ms:.1f} ms")
            print(f"  Latency p95 ........... {result.latency_p95_ms:.1f} ms")
            print(f"  Peak RSS .............. {result.peak_rss_mb:.1f} MB")
            print(f"  Threads ............... {result.thread_count}")
            print(f"  Frames dropped ........ {result.frames_dropped}")
            print(f"  Drop rate ............. {result.drop_rate_pct:.1f}%")
            if result.errors:
                print(f"  Errors ................ {result.errors}")
            print("=" * 60 + "\n")
        sys.exit(1 if result.errors else 0)
    else:
        result = run_multi_stream(
            num_streams=args.num_streams,
            duration_seconds=args.duration,
            detector_latency_ms=args.detector_latency_ms,
            num_detections=args.num_detections,
            width=args.width,
            height=args.height,
            fps_cap=args.fps_cap,
            scheduler_enabled=args.scheduler_enabled,
            scheduler_workers=args.scheduler_workers,
        )
        if args.json:
            print(json.dumps(asdict(result), indent=2))
        else:
            print("\n" + "=" * 60)
            print(f" OmniTrack Multi-Stream Baseline ({args.num_streams} streams)")
            print("=" * 60)
            print(f"  Duration .............. {result.duration_seconds:.1f} s")
            print(f"  Aggregate FPS ......... {result.aggregate_delivered_fps:.1f}")
            print(f"  Global Latency p50 .... {result.global_latency_p50_ms:.1f} ms")
            print(f"  Global Latency p95 .... {result.global_latency_p95_ms:.1f} ms")
            print(f"  Peak RSS .............. {result.peak_rss_mb:.1f} MB")
            print(f"  Threads ............... {result.thread_count}")
            print(f"  Drop rate ............. {result.aggregate_drop_rate_pct:.1f}%")
            print(f"  FPS Variance .......... {result.fps_variance:.2f}")
            if result.errors:
                print(f"  Errors ................ {result.errors}")
            print("=" * 60 + "\n")
        sys.exit(1 if result.errors else 0)


if __name__ == "__main__":
    main()
