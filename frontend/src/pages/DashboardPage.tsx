import { useStreams } from "@/hooks/useStreams";
import { useStreamMetrics } from "@/hooks/useStreamMetrics";
import { StreamGrid } from "@/components/streams/StreamGrid";
import { PlatformSummary } from "@/components/telemetry/PlatformSummary";
import { StreamTelemetry } from "@/components/telemetry/StreamTelemetry";
import { TelemetryEmptyState } from "@/components/telemetry/TelemetryEmptyState";
import type { StreamSummary } from "@/types/api";

/**
 * Drives the ~1Hz metrics poll for a single stream, feeding the telemetry
 * ring buffer. Rendered (headless) per stream so each subscription is
 * isolated; renders the per-stream telemetry panel.
 */
function StreamTelemetrySection({ stream }: { stream: StreamSummary }) {
  useStreamMetrics(stream.stream_id, stream.is_running);
  return <StreamTelemetry stream={stream} />;
}

/**
 * Overview page. Platform summary + per-stream telemetry above the live
 * stream grid. Telemetry is a separate ~1Hz pipeline from the frame viewer.
 */
export function DashboardPage() {
  const { data } = useStreams();
  const streams = data ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-content-primary">Overview</h1>
        <p className="text-sm text-content-muted">
          Platform status, operational telemetry &amp; active inference streams.
        </p>
      </div>

      <PlatformSummary streams={streams} />

      <section className="space-y-3">
        <h2 className="text-xs uppercase tracking-wide text-content-muted">
          Stream telemetry
        </h2>
        {streams.length === 0 ? (
          <TelemetryEmptyState />
        ) : (
          <div className="space-y-3">
            {streams.map((stream) => (
              <StreamTelemetrySection key={stream.stream_id} stream={stream} />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-xs uppercase tracking-wide text-content-muted">
          Streams
        </h2>
        <StreamGrid />
      </section>
    </div>
  );
}
