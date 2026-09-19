#!/usr/bin/env python
"""Run the OmniTrack single-stream performance baseline and save results.

Usage::

    python scripts/perf/run_baseline.py
    python scripts/perf/run_baseline.py --duration 60 --detector-latency-ms 20

Results are written as JSON to ``scripts/perf/results/baseline_<timestamp>.json``
and a human-readable summary is printed to stdout.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

# Ensure the project root is on sys.path so ``tests.perf`` and ``backend``
# are importable even when this script is invoked directly.
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from tests.perf.perf_runner import main as runner_main, run_single_stream


def _results_dir() -> Path:
    d = Path(__file__).resolve().parent / "results"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> None:
    # Reuse the CLI parser from perf_runner for flags, but also save to file.
    import argparse

    parser = argparse.ArgumentParser(
        description="OmniTrack performance baseline runner"
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
    args = parser.parse_args()

    result = run_single_stream(
        duration_seconds=args.duration,
        detector_latency_ms=args.detector_latency_ms,
        num_detections=args.num_detections,
        width=args.width,
        height=args.height,
        fps_cap=args.fps_cap,
    )

    # Print human-readable summary.
    print("\n" + "=" * 60)
    print(" OmniTrack Single-Stream Performance Baseline")
    print("=" * 60)
    print(f"  Duration .............. {result.duration_seconds:.1f} s")
    print(f"  Frames produced ....... {result.total_frames_produced}")
    print(f"  Frames consumed ....... {result.total_frames_consumed}")
    print(f"  Delivered FPS ......... {result.delivered_fps}")
    print(f"  Capture FPS ........... {result.capture_fps}")
    print(f"  Latency p50 ........... {result.latency_p50_ms:.1f} ms")
    print(f"  Latency p95 ........... {result.latency_p95_ms:.1f} ms")
    print(f"  Latency p99 ........... {result.latency_p99_ms:.1f} ms")
    print(f"  Latency mean .......... {result.latency_mean_ms:.1f} ms")
    print(f"  Peak RSS .............. {result.peak_rss_mb:.1f} MB")
    print(f"  Threads ............... {result.thread_count}")
    print(f"  Frames dropped ........ {result.frames_dropped}")
    print(f"  Drop rate ............. {result.drop_rate_pct:.1f}%")
    if result.errors:
        print(f"  Errors ................ {result.errors}")
    print("=" * 60)

    # Save JSON to results directory.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = _results_dir() / f"baseline_{ts}.json"
    payload = asdict(result)
    payload["_timestamp"] = ts
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"\n  Results saved to: {out_path}\n")

    sys.exit(1 if result.errors else 0)


if __name__ == "__main__":
    main()
