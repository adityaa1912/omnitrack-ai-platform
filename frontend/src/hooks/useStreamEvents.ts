/**
 * React adapter for a stream's live event feed.
 *
 * Composes the two event sources into the bounded event store:
 *  - REST backfill (GET /stream/{id}/events) to seed recent history on mount,
 *  - the live events WebSocket (via a dedicated EventStreamClient instance) to
 *    push new events and gap notices as they occur — no polling.
 *
 * Owns one EventStreamClient for the consumer's lifetime (mirroring how
 * useStreamSocket owns a StreamSocket) and tears it down on unmount, so we
 * never hold an idle event connection. A dedicated instance — rather than the
 * module singleton — keeps each mount's connection status isolated and avoids
 * cross-talk if more than one feed is ever mounted.
 *
 * De-duplication by `seq` in the store makes the backfill/live overlap safe
 * regardless of which resolves first.
 */

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { streamsApi } from "@/lib/api/streams";
import { EventStreamClient, type EventClientStatus } from "@/services/events";
import { useEventStore } from "@/store/useEventStore";
import { queryKeys } from "./queryKeys";

export interface UseStreamEventsResult {
  /** Live connection status of the events socket. */
  status: EventClientStatus;
  /** True while the initial history backfill is in flight. */
  isBackfilling: boolean;
  /** True if the history backfill failed (live streaming may still work). */
  isBackfillError: boolean;
}

export function useStreamEvents(
  streamId: string,
  enabled = true,
): UseStreamEventsResult {
  const ingestEvent = useEventStore((s) => s.ingestEvent);
  const ingestGap = useEventStore((s) => s.ingestGap);
  const seedHistory = useEventStore((s) => s.seedHistory);
  const removeStream = useEventStore((s) => s.removeStream);

  const [status, setStatus] = useState<EventClientStatus>("idle");

  // History backfill. Read-only server state, so it lives in Query; the store
  // is seeded from it. staleTime avoids refetch churn on remounts.
  const history = useQuery({
    queryKey: queryKeys.streamEvents(streamId),
    queryFn: () => streamsApi.events(streamId),
    enabled: enabled && Boolean(streamId),
    staleTime: 10_000,
  });

  const historyData = history.data;
  useEffect(() => {
    if (historyData) seedHistory(streamId, historyData);
  }, [historyData, streamId, seedHistory]);

  // Live socket: subscribe BEFORE connecting so no event is missed in the gap
  // between accept and subscribe.
  useEffect(() => {
    if (!enabled || !streamId) return;

    const client = new EventStreamClient({ onStatus: setStatus });
    const unsubscribe = client.subscribe((notification) => {
      if (notification.kind === "event") {
        ingestEvent(streamId, notification.event);
      } else {
        ingestGap(streamId, notification.dropped);
      }
    });
    client.connect(streamId);

    return () => {
      unsubscribe();
      client.disconnect();
      setStatus("closed");
      // Free retained events for a stream no one is viewing (bounded memory).
      removeStream(streamId);
    };
  }, [streamId, enabled, ingestEvent, ingestGap, removeStream]);

  return {
    status,
    isBackfilling: history.isLoading,
    isBackfillError: history.isError,
  };
}
