import { memo } from "react";
import type { StreamEvent } from "@/services/events";
import {
  eventTypeLabel,
  formatEventTimestamp,
  severityStyle,
} from "@/lib/events/eventMeta";
import { EventSeverityBadge } from "./EventSeverityBadge";
import { cn } from "@/lib/utils/cn";

/**
 * One event in the feed. Memoized: events are immutable and keyed by `seq`, so
 * a row never needs to rerender once mounted — only new rows are added at the
 * top.
 */
export const EventRow = memo(function EventRow({ event }: { event: StreamEvent }) {
  const style = severityStyle(event.severity);

  return (
    <li
      className={cn(
        "flex flex-col gap-1 border-l-2 bg-surface-100/40 px-3 py-2",
        style.rowAccentClass,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <EventSeverityBadge severity={event.severity} />
          <span className="truncate text-xs font-medium text-content-primary">
            {eventTypeLabel(event.event_type)}
          </span>
        </div>
        <span className="shrink-0 font-mono text-[10px] tabular-nums text-content-muted">
          t+{formatEventTimestamp(event.timestamp)}
        </span>
      </div>

      {event.message && (
        <p className="truncate text-xs text-content-secondary">{event.message}</p>
      )}

      <div className="flex items-center gap-2 font-mono text-[10px] text-content-muted">
        {event.track_id !== null && <span>track {event.track_id}</span>}
        {event.class_name && <span>{event.class_name}</span>}
        <span className="ml-auto">#{event.seq}</span>
      </div>
    </li>
  );
});
