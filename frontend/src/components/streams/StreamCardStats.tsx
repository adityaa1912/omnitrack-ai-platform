import type { StreamMetrics } from "@/types/api";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-content-muted">
        {label}
      </span>
      <span className="font-mono text-sm text-content-primary">{value}</span>
    </div>
  );
}

/** Compact metric summary shown on each card (full charts come later). */
export function StreamCardStats({ metrics }: { metrics: StreamMetrics }) {
  return (
    <div className="grid grid-cols-3 gap-3">
      <Stat label="FPS" value={metrics.fps.toFixed(1)} />
      <Stat label="Latency" value={`${metrics.inference_time_avg_ms.toFixed(0)}ms`} />
      <Stat label="Detections" value={metrics.total_detections.toLocaleString()} />
    </div>
  );
}
