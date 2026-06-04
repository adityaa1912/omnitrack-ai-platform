import { useMemo } from "react";
import type { StreamSummary } from "@/types/api";
import { MetricCard } from "./MetricCard";
import { formatCount, formatFps, formatMs } from "@/lib/utils/format";

/**
 * Aggregate platform metrics across all streams. Computed once per render
 * from the streams list (server state) and memoized on its identity. This is
 * a per-render reduction, not a per-frame loop, so it stays cheap.
 */
export function PlatformSummary({ streams }: { streams: StreamSummary[] }) {
  const agg = useMemo(() => {
    const running = streams.filter((s) => s.is_running);
    const totalFps = running.reduce((acc, s) => acc + s.metrics.fps, 0);
    const totalDetections = streams.reduce(
      (acc, s) => acc + s.metrics.total_detections,
      0,
    );
    const avgLatency =
      running.length > 0
        ? running.reduce((acc, s) => acc + s.metrics.inference_time_avg_ms, 0) /
          running.length
        : 0;
    return {
      active: running.length,
      total: streams.length,
      totalFps,
      totalDetections,
      avgLatency,
    };
  }, [streams]);

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <MetricCard
        label="Active streams"
        value={`${agg.active}`}
        unit={`/ ${agg.total}`}
        tone={agg.active > 0 ? "accent" : "default"}
      />
      <MetricCard label="Aggregate throughput" value={formatFps(agg.totalFps)} unit="fps" />
      <MetricCard label="Total detections" value={formatCount(agg.totalDetections)} />
      <MetricCard
        label="Avg latency"
        value={agg.active > 0 ? formatMs(agg.avgLatency) : "\u2014"}
      />
    </div>
  );
}
