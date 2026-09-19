"""Single-stream throughput test — first empirical performance baseline.

Marked ``@pytest.mark.perf`` so it is **skipped** by the default CI run
(``pytest tests/ -q``).  Run explicitly with::

    pytest tests/perf/test_single_stream_throughput.py -v -s

Or select by marker::

    pytest -m perf -v -s

The test is deliberately loose: it asserts *correctness* (no crashes, frames
flow, metrics update) rather than hard performance gates.  The actual numbers
printed to stdout are the real deliverable — they form the first baseline.
"""

from __future__ import annotations

import pytest

from .perf_runner import run_single_stream, PerfResult

# Register the marker so pytest does not warn about unknown markers.
pytestmark = pytest.mark.perf


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------

def _run_and_print(duration: float = 10.0, **kwargs) -> PerfResult:
    """Run the harness, print a summary, and return the result."""
    result = run_single_stream(duration_seconds=duration, **kwargs)

    print(f"\n--- Perf result (duration={result.duration_seconds}s) ---")
    print(f"  Delivered FPS:   {result.delivered_fps}")
    print(f"  Capture FPS:     {result.capture_fps}")
    print(f"  Latency p50:     {result.latency_p50_ms:.1f} ms")
    print(f"  Latency p95:     {result.latency_p95_ms:.1f} ms")
    print(f"  Latency p99:     {result.latency_p99_ms:.1f} ms")
    print(f"  Peak RSS:        {result.peak_rss_mb:.1f} MB")
    print(f"  Threads:         {result.thread_count}")
    print(f"  Frames produced: {result.total_frames_produced}")
    print(f"  Frames consumed: {result.total_frames_consumed}")
    print(f"  Dropped:         {result.frames_dropped} ({result.drop_rate_pct}%)")
    if result.errors:
        print(f"  ERRORS:          {result.errors}")
    print("---\n")

    return result


# -------------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------------

class TestSingleStreamThroughput:
    """Sanity and baseline tests for a single synthetic stream."""

    def test_stream_delivers_frames(self) -> None:
        """The pipeline produces and delivers frames without crashing."""
        result = _run_and_print(duration=10.0, detector_latency_ms=10.0)

        assert not result.errors, f"Unexpected errors: {result.errors}"
        assert result.total_frames_produced > 0, "Source produced no frames"
        assert result.total_frames_consumed > 0, "Consumer received no frames"
        assert result.delivered_fps > 0, "Delivered FPS must be > 0"

    def test_latency_is_measured(self) -> None:
        """Latency percentiles are populated and plausible."""
        result = _run_and_print(duration=10.0, detector_latency_ms=5.0)

        assert not result.errors
        # With 5ms mock latency, p50 should be at least a few ms.
        assert result.latency_p50_ms > 0, "p50 latency is 0 — measurement broken"
        # p99 should be >= p50 (monotonic percentiles).
        assert result.latency_p99_ms >= result.latency_p50_ms

    def test_drop_rate_bounded(self) -> None:
        """Frame drop rate stays below 90% (very loose first gate).

        The mock detector at 10ms latency can theoretically do ~100 FPS,
        and the source runs uncapped, so some drops are expected from the
        bounded output queue.  This assertion only catches catastrophic
        pipeline stalls.
        """
        result = _run_and_print(duration=10.0, detector_latency_ms=10.0)

        assert not result.errors
        assert result.drop_rate_pct < 90.0, (
            f"Drop rate {result.drop_rate_pct}% exceeds 90% — pipeline stalled"
        )

    def test_zero_latency_detector(self) -> None:
        """With 0ms detector latency, measure pure pipeline overhead."""
        result = _run_and_print(duration=10.0, detector_latency_ms=0.0)

        assert not result.errors
        assert result.total_frames_consumed > 0
        # Pure overhead: delivered FPS should be noticeably higher than the
        # 10ms-latency case.  We don't gate on a number — just confirm it runs.
        print(f"  [overhead] Delivered FPS with 0ms detector: {result.delivered_fps}")

    def test_fps_capped_source(self) -> None:
        """With source FPS capped at 30, delivered FPS should not exceed ~35.

        Allows a small margin above the cap for timing jitter.
        """
        result = _run_and_print(
            duration=10.0, detector_latency_ms=5.0, fps_cap=30.0
        )

        assert not result.errors
        assert result.delivered_fps <= 40.0, (
            f"Delivered {result.delivered_fps} FPS with 30 FPS cap — source leak"
        )

    def test_memory_bounded(self) -> None:
        """Peak RSS stays under 2 GB for a single synthetic stream.

        This is extremely generous — the real concern is a leak, not
        absolute size.  A single 640×480 stream with mock inference
        should use well under 500 MB.
        """
        result = _run_and_print(duration=10.0, detector_latency_ms=10.0)

        assert not result.errors
        assert result.peak_rss_mb < 2048, (
            f"Peak RSS {result.peak_rss_mb} MB exceeds 2 GB — possible leak"
        )
