/**
 * Live event store (Zustand).
 *
 * A bounded, newest-first buffer of derived events per stream. This is the
 * retention layer the event transport deliberately lacks: `services/events.ts`
 * is a pure transport and holds no history, so this store decides what to keep.
 *
 * Design properties (mirroring the backend EventBuffer's guarantees on the
 * client):
 *  - Bounded memory: at most `MAX_EVENTS` per stream; the oldest is evicted
 *    once full. A stream can run for hours without this growing without bound.
 *  - De-duplicated by `seq`: the monotonic, gap-free key the backend stamps on
 *    every record. Live WS events and the REST history backfill overlap freely;
 *    an event already retained is never inserted twice.
 *  - Ordered by `seq` descending (newest-first) regardless of arrival order, so
 *    a late history backfill and out-of-order delivery still render correctly.
 *  - Gap-aware: the coalesced `{type:"gap"}` notices from the transport are
 *    accumulated into a per-stream `dropped` counter the UI can surface.
 *
 * Keyed by stream_id so an update for one stream never rerenders consumers of
 * another. Read narrow slices via the exported selectors.
 */

import { create } from "zustand";
import type { StreamEvent } from "@/services/events";

/** Per-stream retention cap. Bounds memory independent of stream duration. */
const MAX_EVENTS = 200;

interface StreamEventState {
  /** Retained events, newest-first (descending `seq`), capped at MAX_EVENTS. */
  events: StreamEvent[];
  /** `seq`s currently retained, for O(1) de-duplication. Kept in sync with `events`. */
  seen: Set<number>;
  /** Total events the server reported dropping for a slow client (coalesced gaps). */
  dropped: number;
}

interface EventStoreState {
  byStream: Record<string, StreamEventState>;
  /** Ingest a single live event (idempotent by `seq`). */
  ingestEvent: (streamId: string, event: StreamEvent) => void;
  /** Record a gap notice: `dropped` events were lost before the next delivery. */
  ingestGap: (streamId: string, dropped: number) => void;
  /** Merge a newest-first history batch (backfill); de-duplicated against live. */
  seedHistory: (streamId: string, events: StreamEvent[]) => void;
  /** Drop retained events for a stream but keep the key (feed reset). */
  clear: (streamId: string) => void;
  /** Forget a stream entirely (frees its retained history). */
  removeStream: (streamId: string) => void;
}

function emptyState(): StreamEventState {
  return { events: [], seen: new Set<number>(), dropped: 0 };
}

/**
 * Insert one event into a per-stream state, preserving newest-first order,
 * de-duplicating by `seq`, and enforcing the retention cap. Returns the SAME
 * reference when the event is a duplicate, so callers can skip a store update
 * (no needless rerender).
 */
function addEvent(prev: StreamEventState, event: StreamEvent): StreamEventState {
  if (prev.seen.has(event.seq)) return prev;

  const events = prev.events.slice();
  // Fast path for the common live case (strictly newer than the head).
  let i = 0;
  while (i < events.length && events[i].seq > event.seq) i += 1;
  events.splice(i, 0, event);

  const seen = new Set(prev.seen);
  seen.add(event.seq);

  // Evict oldest (tail) beyond the cap, keeping `seen` in lock-step.
  while (events.length > MAX_EVENTS) {
    const removed = events.pop();
    if (removed) seen.delete(removed.seq);
  }

  return { ...prev, events, seen };
}

export const useEventStore = create<EventStoreState>((set) => ({
  byStream: {},

  ingestEvent: (streamId, event) =>
    set((state) => {
      const prev = state.byStream[streamId] ?? emptyState();
      const next = addEvent(prev, event);
      if (next === prev) return state; // duplicate seq: no change
      return { byStream: { ...state.byStream, [streamId]: next } };
    }),

  ingestGap: (streamId, dropped) =>
    set((state) => {
      if (dropped <= 0) return state;
      const prev = state.byStream[streamId] ?? emptyState();
      return {
        byStream: {
          ...state.byStream,
          [streamId]: { ...prev, dropped: prev.dropped + dropped },
        },
      };
    }),

  seedHistory: (streamId, incoming) =>
    set((state) => {
      const prev = state.byStream[streamId] ?? emptyState();
      let next = prev;
      for (const event of incoming) next = addEvent(next, event);
      if (next === prev) return state; // all duplicates: no change
      return { byStream: { ...state.byStream, [streamId]: next } };
    }),

  clear: (streamId) =>
    set((state) => {
      if (!(streamId in state.byStream)) return state;
      return { byStream: { ...state.byStream, [streamId]: emptyState() } };
    }),

  removeStream: (streamId) =>
    set((state) => {
      if (!(streamId in state.byStream)) return state;
      const next = { ...state.byStream };
      delete next[streamId];
      return { byStream: next };
    }),
}));

/**
 * Shared stable empty array. Returning a fresh `[]` per selector call would
 * change reference every render and defeat reference-based memoization, forcing
 * needless feed rerenders. One frozen constant keeps the empty case stable.
 */
const EMPTY_EVENTS: readonly StreamEvent[] = Object.freeze([]);

export const selectStreamEvents =
  (streamId: string) =>
  (state: EventStoreState): readonly StreamEvent[] =>
    state.byStream[streamId]?.events ?? EMPTY_EVENTS;

export const selectDroppedCount =
  (streamId: string) =>
  (state: EventStoreState): number =>
    state.byStream[streamId]?.dropped ?? 0;
