import { useMemo } from "react";
import type { StreamSummary } from "@/types/api";
import { useTelemetryStore, selectTelemetry } from "@/store/useTelemetryStore";
import { useStreamStore } from "@/store/useStreamStore";
import type { SocketStatus } from "@/lib/ws/StreamSocket";
import { Panel } from "@/components/ui/Panel";
import { MetricCard } from "./MetricCard";
import { Sparkline } from "./Sparkline";
import { StreamStatusBadge } from "@/components/ui/StreamStatusBadge";
import { formatCount, formatFps, formatMs } from "@/lib/utils/format";

/** Latency thresholds (ms) for semantic coloring. */
const LATENCY_WARN = 80;
const LATENCY_ERROR = 150;

/**
 * Per-stream telemetry panel. Subscribes to ONE stream's ring buffer via the
 * narrow selector, so it updates at the ~1Hz metrics cadence and never on
 * unrelated streams' samples. No coupling to the frame render loop.
 */
export function StreamTelemetry({ stream }: { stream: StreamSummary }) {
  const { stream_id } = stream;
  const data = useTelemetryStore(selectTelemetry(stream_id));
  const status: SocketStatus =
    useStreamStore((s) => s.streams[stream_id]?.status) ?? "idle";

  const latest = data.length > 0 ? data[data.length - 1] : null;

  const latencyTone = useMemo<"default" | "warn" | "error">(() => {
    const ms = latest?.inferenceMs ?? 0;
    if (ms >= LATENCY_ERROR) return "error";
    if (ms >= LATENCY_WARN) return "warn";
    return "default";
  }, [latest?.inferenceMs]);

  return (
    <Panel className="flex flex-col gap-4 p-4">
      <div className="flex items-center justify-between">
        <h3 className="truncate font-mono text-sm font-semibold text-content-primary">
          {stream_id}
        </h3>
        <StreamStatusBadge status={status} />
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard
          label="Throughput"
          value={latest ? formatFps(latest.fps) : "\u2014"}
          unit="fps"
          tone="accent"
          chart={<Sparkline data={data} field="fps" color="#3da9fc" />}
        />
        <MetricCard
          label="Inference latency"
          value={latest ? formatMs(latest.inferenceMs) : "\u2014"}
          tone={latencyTone}
          chart={
            <Sparkline
              data={data}
              field="inferenceMs"
              color={
                latencyTone === "error"
                  ? "#f4607a"
                  : latencyTone === "warn"
                    ? "#f5b14c"
                    : "#9aa7b8"
              }
            />
          }
        />
        <MetricCard
          label="Detection rate"
          value={
            latest ? formatCount(latest.totalDetections) : "\u2014"
          }
          unit="total"
          chart={<Sparkline data={data} field="detectionRate" color="#22d39a" />}
        />
        <MetricCard
          label="Frames processed"
          value={latest ? formatCount(latest.totalFrames) : "\u2014"}
          unit="total"
        />
      </div>
    </Panel>
  );
}
