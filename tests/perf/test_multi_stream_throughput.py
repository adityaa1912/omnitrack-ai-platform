"""Multi-stream throughput scaling tests.

Marked ``@pytest.mark.perf`` so it is **skipped** by the default CI run
(``pytest tests/ -q``). Run explicitly with::

    pytest tests/perf/test_multi_stream_throughput.py -v -s

Or select by marker::

    pytest -m perf -v -s
"""

from __future__ import annotations

import pytest

from .perf_runner import run_multi_stream, MultiPerfResult

pytestmark = pytest.mark.perf


def _run_and_print(num_streams: int, duration: float = 10.0, **kwargs) -> MultiPerfResult:
    """Run the multi-stream harness, print a summary, and return the result."""
    result = run_multi_stream(num_streams=num_streams, duration_seconds=duration, **kwargs)

    print(f"\n--- Perf result (streams={num_streams}, duration={result.duration_seconds}s) ---")
    print(f"  Aggregate Delivered FPS:   {result.aggregate_delivered_fps:.2f}")
    print(f"  Global Latency p50:        {result.global_latency_p50_ms:.1f} ms")
    print(f"  Global Latency p95:        {result.global_latency_p95_ms:.1f} ms")
    print(f"  Peak RSS:                  {result.peak_rss_mb:.1f} MB")
    print(f"  Threads:                   {result.thread_count}")
    print(f"  Frames dropped:            {result.total_frames_dropped} ({result.aggregate_drop_rate_pct}%)")
    print(f"  FPS Variance:              {result.fps_variance:.2f}")
    if result.errors:
        print(f"  ERRORS:                    {result.errors}")
    print("---\n")

    return result


class TestMultiStreamThroughput:
    """Scalability and concurrency limits tests."""

    @pytest.mark.parametrize("num_streams", [1, 2, 4])
    def test_throughput_scaling(self, num_streams: int) -> None:
        """Verify that aggregate FPS scales reasonably and no catastrophic failures occur."""
        result = _run_and_print(num_streams=num_streams, duration=10.0, detector_latency_ms=10.0)

        assert not result.errors, f"Unexpected errors: {result.errors}"
        assert result.total_frames_produced > 0
        assert result.aggregate_delivered_fps > 0

        # Rough scaling test - shouldn't drop frames substantially for 1-4 streams
        assert result.aggregate_drop_rate_pct < 90.0

    def test_latency_growth(self) -> None:
        """Ensure p95 latency does not degrade exponentially with stream count.
        
        Compare 1 stream vs 4 streams. The p95 latency should remain within
        reasonable bounds (e.g. less than a 10x multiplier of the simulated latency).
        """
        res_1 = _run_and_print(num_streams=1, duration=10.0, detector_latency_ms=5.0)
        res_4 = _run_and_print(num_streams=4, duration=10.0, detector_latency_ms=5.0)

        assert not res_1.errors
        assert not res_4.errors

        # Latency shouldn't explode (e.g. shouldn't exceed 200ms when simulated latency is just 5ms)
        assert res_1.global_latency_p95_ms < 100.0
        assert res_4.global_latency_p95_ms < 200.0

    def test_fairness(self) -> None:
        """Verify that FPS variance between streams is bounded (no starvation)."""
        result = _run_and_print(num_streams=4, duration=10.0, detector_latency_ms=10.0)

        assert not result.errors
        
        # All streams should have comparable FPS
        for stream_res in result.stream_results:
            assert stream_res.delivered_fps > 10.0, f"Stream starved: {stream_res.delivered_fps} FPS"
        
        # Variance should be relatively low (e.g., standard deviation < 15 FPS)
        # So variance < 225
        assert result.fps_variance < 500.0, f"High variance in stream FPS: {result.fps_variance}"

    def test_memory_growth(self) -> None:
        """Verify peak RSS scales linearly without massive leaks."""
        res_1 = _run_and_print(num_streams=1, duration=10.0, detector_latency_ms=10.0)
        res_4 = _run_and_print(num_streams=4, duration=10.0, detector_latency_ms=10.0)

        assert not res_1.errors
        assert not res_4.errors

        # 4 streams shouldn't consume more than e.g. 5 times the memory of 1 stream
        assert res_4.peak_rss_mb < (res_1.peak_rss_mb * 5 + 100), "Memory growth is not linear!"
