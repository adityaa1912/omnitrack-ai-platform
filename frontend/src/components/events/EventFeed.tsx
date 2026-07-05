import { AlertTriangle, Radio } from "lucide-react";
import {
  useEventStore,
  selectStreamEvents,
  selectDroppedCount,
} from "@/store/useEventStore";
import { useStreamEvents } from "@/hooks/useStreamEvents";
import type { EventClientStatus } from "@/services/events";
import { Panel } from "@/components/ui/Panel";
import { StatusDot } from "@/components/ui/StatusDot";
import { Spinner } from "@/components/ui/Spinner";
import { EventRow } from "./EventRow";
import { cn } from "@/lib/utils/cn";

/** Map the event socket status to a dot tone + label (distinct from frames). */
const STATUS_MAP: Record<
  EventClientStatus,
  { dot: "live" | "idle" | "warn" | "error"; label: string }
> = {
  idle: { dot: "idle", label: "idle" },
  connecting: { dot: "warn", label: "connecting" },
  open: { dot: "live", label: "live" },
  reconnecting: { dot: "warn", label: "reconnecting" },
  closed: { dot: "idle", label: "closed" },
};

/**
 * Live semantic event feed for one stream. Owns the event subscription for its
 * lifetime via useStreamEvents (history backfill + live WS → bounded store) and
 * renders the retained events newest-first. Purely a consumer of the store; it
 * never touches the frame path.
 */
export function EventFeed({
  streamId,
  enabled = true,
  className,
}: {
  streamId: string;
  enabled?: boolean;
  className?: string;
}) {
  const { status, isBackfilling, isBackfillError } = useStreamEvents(
    streamId,
    enabled,
  );
  const events = useEventStore(selectStreamEvents(streamId));
  const dropped = useEventStore(selectDroppedCount(streamId));

  const statusView = STATUS_MAP[status];
  const showInitialLoader = isBackfilling && events.length === 0;

  return (
    <Panel className={cn("flex flex-col overflow-hidden", className)}>
      <header className="flex items-center justify-between gap-3 border-b border-border-subtle px-4 py-3">
        <div className="flex items-center gap-2">
          <Radio className="h-4 w-4 text-content-muted" />
          <h3 className="text-sm font-semibold text-content-primary">Events</h3>
          <span className="font-mono text-[10px] text-content-muted">
            {events.length}
          </span>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-border-subtle bg-surface-200/60 px-2 py-0.5">
          <StatusDot status={statusView.dot} />
          <span className="font-mono text-[10px] uppercase tracking-wide text-content-secondary">
            {statusView.label}
          </span>
        </span>
      </header>

      {dropped > 0 && (
        <div className="flex items-center gap-2 border-b border-status-warn/20 bg-status-warn/10 px-4 py-2 text-status-warn">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span className="text-xs">
            {dropped} event{dropped === 1 ? "" : "s"} dropped (slow connection)
          </span>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {showInitialLoader ? (
          <div className="flex items-center justify-center gap-2 py-10 text-content-muted">
            <Spinner className="h-4 w-4" />
            <span className="text-xs">Loading history…</span>
          </div>
        ) : events.length === 0 ? (
          <EmptyState isError={isBackfillError} />
        ) : (
          <ul className="flex flex-col gap-1.5 p-2">
            {events.map((event) => (
              <EventRow key={event.seq} event={event} />
            ))}
          </ul>
        )}
      </div>
    </Panel>
  );
}

function EmptyState({ isError }: { isError: boolean }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 py-10 text-center">
      <span className="font-mono text-[10px] uppercase tracking-widest text-content-muted">
        {isError ? "history unavailable" : "no events yet"}
      </span>
      <span className="text-xs text-content-muted">
        {isError
          ? "Live events will still appear as they occur."
          : "Derived events will appear here in real time."}
      </span>
    </div>
  );
}
