import { useState } from "react";
import { Square } from "lucide-react";
import type { StreamSummary } from "@/types/api";
import { Panel } from "@/components/ui/Panel";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { StreamStatusBadge } from "@/components/ui/StreamStatusBadge";
import { StreamThumbnail } from "./StreamThumbnail";
import { StreamCardStats } from "./StreamCardStats";
import { useStreamSocket } from "@/hooks/useStreamSocket";
import { useStreamStore, selectStreamRuntime } from "@/store/useStreamStore";
import { useStopStream } from "@/hooks/useStreams";
import { cn } from "@/lib/utils/cn";

/**
 * A single stream tile. OWNS its socket for its lifetime via useStreamSocket;
 * unmounting (e.g. backend removes the stream) disposes the socket through
 * the hook's cleanup. Server lifecycle (is_running) and live connection
 * status (socket) are shown distinctly.
 */
export function StreamCard({ stream }: { stream: StreamSummary }) {
  const { stream_id, source, is_running, metrics } = stream;

  // Card owns the socket subscription.
  useStreamSocket(stream_id, is_running);

  const runtime = useStreamStore(selectStreamRuntime(stream_id));
  const socketStatus = runtime?.status ?? "idle";

  const stop = useStopStream();
  const [stopping, setStopping] = useState(false);

  const handleStop = () => {
    setStopping(true); // optimistic local state
    stop.mutate(stream_id, {
      onError: () => setStopping(false),
    });
  };

  return (
    <Panel
      className={cn(
        "flex flex-col gap-4 p-4 transition-opacity",
        stopping && "pointer-events-none opacity-60",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-mono text-sm font-semibold text-content-primary">
            {stream_id}
          </h3>
          <p className="truncate text-xs text-content-muted">{source}</p>
        </div>
        <StreamStatusBadge status={socketStatus} />
      </div>

      <StreamThumbnail />

      <StreamCardStats metrics={metrics} />

      <div className="flex items-center justify-between border-t border-border-subtle pt-3">
        <span className="text-[10px] uppercase tracking-wide text-content-muted">
          {is_running ? "running" : "stopped"}
        </span>
        <Button
          variant="danger"
          size="sm"
          onClick={handleStop}
          disabled={stopping || !is_running}
        >
          {stopping ? <Spinner className="h-3.5 w-3.5" /> : <Square className="h-3.5 w-3.5" />}
          {stopping ? "Stopping" : "Stop"}
        </Button>
      </div>
    </Panel>
  );
}
