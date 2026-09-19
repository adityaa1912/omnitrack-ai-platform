#!/usr/bin/env python
"""Run the OmniTrack multi-stream performance baseline.

Executes a scaling matrix for 1, 2, 4, 8, and 12 concurrent streams.
Results are logged to a JSON file in `scripts/perf/results/`.

Usage::
    python scripts/perf/run_multi_baseline.py
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

# Ensure the project root is on sys.path
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from tests.perf.perf_runner import run_multi_stream


def _results_dir() -> Path:
    d = Path(__file__).resolve().parent / "results"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> None:
    parser = argparse.ArgumentParser(description="Run OmniTrack multi-stream baseline")
    parser.add_argument("--duration", type=float, default=30.0, help="Test duration in seconds")
    parser.add_argument("--detector-latency-ms", type=float, default=10.0, help="Simulated detector latency")
    parser.add_argument("--scheduler-enabled", action="store_true", help="Enable InferenceScheduler")
    parser.add_argument("--scheduler-workers", type=int, default=4, help="Scheduler worker count (if enabled)")
    args = parser.parse_args()

    duration = args.duration
    detector_latency_ms = args.detector_latency_ms
    scheduler_enabled = args.scheduler_enabled
    scheduler_workers = args.scheduler_workers
    
    stream_counts = [1, 2, 4, 8, 12]
    
    results = []
    
    print("\n" + "=" * 60)
    print(" OmniTrack Multi-Stream Scalability Baseline")
    print("=" * 60)
    print(f"  Duration per test: {duration}s")
    print(f"  Simulated latency: {detector_latency_ms}ms")
    if scheduler_enabled:
        print(f"  Scheduler: ENABLED ({scheduler_workers} workers)")
    else:
        print(f"  Scheduler: DISABLED (1 thread per stream)")
    print("=" * 60 + "\n")
    
    # Print markdown table header
    print("| Streams | Agg FPS | Avg p50 (ms) | Avg p95 (ms) | Peak RSS (MB) | Threads | Drops (%) |")
    print("|---------|---------|--------------|--------------|---------------|---------|-----------|")

    errors = False

    for n in stream_counts:
        # Run test for N streams
        res = run_multi_stream(
            num_streams=n,
            duration_seconds=duration,
            detector_latency_ms=detector_latency_ms,
            scheduler_enabled=scheduler_enabled,
            scheduler_workers=scheduler_workers,
        )
        
        results.append(res)
        
        # Log row
        print(f"| {n:<7} | {res.aggregate_delivered_fps:<7.1f} | {res.global_latency_p50_ms:<12.1f} | {res.global_latency_p95_ms:<12.1f} | {res.peak_rss_mb:<13.1f} | {res.thread_count:<7} | {res.aggregate_drop_rate_pct:<9.1f} |")
        sys.stdout.flush()
        
        if res.errors:
            print(f"\n  [!] Errors for {n} streams: {res.errors}\n")
            errors = True

    # Save JSON
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = _results_dir() / f"multi_baseline_{ts}.json"
    
    payload = {
        "_timestamp": ts,
        "duration_seconds": duration,
        "detector_latency_ms": detector_latency_ms,
        "results": [asdict(r) for r in results]
    }
    
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        
    print(f"\n  Results saved to: {out_path}\n")
    
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
