import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useStreams } from "@/hooks/useStreams";
import { useStreamSocket } from "@/hooks/useStreamSocket";
import { useStreamMetrics } from "@/hooks/useStreamMetrics";
import { useStreamRegions } from "@/hooks/useStreamRegions";
import { useStreamStore, selectStreamRuntime } from "@/store/useStreamStore";
import { StreamViewer } from "@/components/viewer/StreamViewer";
import { RegionOverlay } from "@/components/regions/RegionOverlay";
import { RegionEditPanel } from "@/components/regions/RegionEditPanel";
import { StreamTelemetry } from "@/components/telemetry/StreamTelemetry";
import { EventFeed } from "@/components/events/EventFeed";
import { DetectionsPanel } from "@/components/detections/DetectionsPanel";
import { Panel } from "@/components/ui/Panel";
import { StreamStatusBadge } from "@/components/ui/StreamStatusBadge";

/**
 * Single-stream detail view: the live frame viewer and telemetry on the left,
 * the live semantic event feed on the right. This page composes the frame
 * pipeline (useStreamSocket → viewer) and the event pipeline (EventFeed →
 * useStreamEvents) side by side while keeping them fully independent — the two
 * never share a socket, store, or render path.
 */
export function StreamDetailPage() {
  const { id = "" } = useParams();

  const { data } = useStreams();
  const stream = data?.find((s) => s.stream_id === id) ?? null;
  const isRunning = stream?.is_running ?? false;

  // Establish the frame socket + metrics poll only while the stream is running.
  useStreamSocket(id, isRunning);
  useStreamMetrics(id, isRunning);
  const { data: regions } = useStreamRegions(id, isRunning);

  const runtime = useStreamStore(selectStreamRuntime(id));
  const socketStatus = runtime?.status ?? "idle";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link
            to="/streams"
            className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border-subtle text-content-secondary transition-colors hover:bg-surface-200 hover:text-content-primary"
            aria-label="Back to streams"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div className="min-w-0">
            <h1 className="truncate font-mono text-lg font-semibold text-content-primary">
              {id}
            </h1>
            <p className="truncate text-xs text-content-muted">
              {stream ? stream.source : "stream not in active list"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wide text-content-muted">
            {isRunning ? "running" : "stopped"}
          </span>
          <StreamStatusBadge status={socketStatus} />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Panel className="overflow-hidden p-0">
            <div className="relative aspect-video w-full bg-surface-0">
              <StreamViewer streamId={id} />
              <RegionOverlay regions={regions} />
            </div>
          </Panel>
          {stream && <StreamTelemetry stream={stream} />}
          <DetectionsPanel
            streamId={id}
            enabled={isRunning}
            className="max-h-80"
          />
          {isRunning && <RegionEditPanel streamId={id} regions={regions} />}
        </div>

        <EventFeed
          streamId={id}
          enabled={isRunning}
          className="h-[70vh] lg:col-span-1"
        />
      </div>
    </div>
  );
}
