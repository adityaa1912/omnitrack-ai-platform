/**
 * Live event stream client.
 *
 * Connects to the backend live-event WebSocket (`/stream/{id}/events/ws`),
 * parses the two message kinds it emits (`event` and `gap`), and fans them out
 * to any number of subscribers. One connection is multiplexed to every UI
 * component that subscribes — components never own a socket.
 *
 * Deliberately framework-agnostic: no React, no DOM beyond the WebSocket it
 * opens. All transport policy (reconnect, backoff, fan-out, teardown) lives
 * here so it can be tested in isolation and reused from anywhere.
 *
 * This is a transport, not a store: it holds no event history. Subscribers
 * (e.g. a zustand store in a later commit) decide what to retain. Subscriptions
 * are independent of the connection lifecycle — they survive reconnects and
 * stream switches, so a component can subscribe once and stay attached.
 */

import { env } from "@/config/env";

// --- wire contract ---------------------------------------------------------
// Defined here (not in types/api.ts) so this client stays self-contained.
// Mirrors the backend `EventResponse` shape (Event.to_dict() + the buffer
// `seq`) and the two frames the events WebSocket sends.

/** A single derived event record, as carried over the wire. */
export interface StreamEvent {
  seq: number;
  stream_id: string;
  event_type: string;
  severity: string;
  severity_rank: number;
  frame_id: number;
  timestamp: number;
  track_id: number | null;
  class_name: string | null;
  message: string;
  metadata: Record<string, unknown>;
}

/** `{"type":"event","event": <record>}` — one newly derived event. */
export interface EventWsEventMessage {
  type: "event";
  event: StreamEvent;
}

/**
 * `{"type":"gap","dropped": N}` — the server dropped N events for this slow
 * client and coalesced them into a single notice. Surfaced so the UI can show
 * a discontinuity rather than silently lying about completeness.
 */
export interface EventWsGapMessage {
  type: "gap";
  dropped: number;
}

export type EventWsMessage = EventWsEventMessage | EventWsGapMessage;

/**
 * What a subscriber receives: either a live event or a gap marker. A
 * discriminated union so callers handle both without a second channel.
 */
export type EventNotification =
  | { kind: "event"; event: StreamEvent }
  | { kind: "gap"; dropped: number };

export type EventListener = (notification: EventNotification) => void;

export type EventClientStatus =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed";

/**
 * Minimal structural view of the parts of `WebSocket` this client uses. Lets
 * tests inject a fake socket and keeps the client off the global DOM type.
 */
export interface EventSocketLike {
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onerror: (() => void) | null;
  onclose: (() => void) | null;
  close(): void;
}

export interface EventStreamClientOptions {
  /** Base reconnect delay; the backoff doubles from here. Default 1000ms. */
  baseDelayMs?: number;
  /** Upper bound on the (pre-jitter) reconnect delay. Default 15000ms. */
  maxDelayMs?: number;
  /** Socket constructor seam (default: native WebSocket). */
  socketFactory?: (url: string) => EventSocketLike;
  /** URL builder seam (default: derived from env + window origin). */
  urlFactory?: (streamId: string) => string;
  /** Notified on every connection-status transition (deduped). */
  onStatus?: (status: EventClientStatus) => void;
}

/** Build the events WebSocket URL, mirroring the frame socket's scheme. */
function buildEventWsUrl(streamId: string): string {
  const base = env.wsBaseUrl.replace(/\/$/, "");
  const path = `/stream/${encodeURIComponent(streamId)}/events/ws`;
  const query = env.apiKey ? `?api_key=${encodeURIComponent(env.apiKey)}` : "";
  if (/^wss?:\/\//i.test(base)) return `${base}${path}${query}`;
  const origin = window.location.origin.replace(/^http/i, "ws");
  return `${origin}${base}${path}${query}`;
}

/** Validate and narrow an unknown parsed payload into a notification. */
function toNotification(value: unknown): EventNotification | null {
  if (typeof value !== "object" || value === null) return null;
  const type = (value as { type?: unknown }).type;

  if (type === "event") {
    const event = (value as { event?: unknown }).event;
    if (typeof event !== "object" || event === null) return null;
    if (typeof (event as { seq?: unknown }).seq !== "number") return null;
    return { kind: "event", event: event as StreamEvent };
  }

  if (type === "gap") {
    const dropped = (value as { dropped?: unknown }).dropped;
    if (typeof dropped !== "number") return null;
    return { kind: "gap", dropped };
  }

  return null;
}

/**
 * A single live-event connection with a shared subscriber registry.
 *
 * Lifecycle: `connect(streamId)` opens (or switches) the socket;
 * `disconnect()` closes it intentionally. Reconnect after an unexpected drop
 * is automatic with exponential backoff + full jitter. Subscribers added via
 * `subscribe` outlive any connection and are only removed by `unsubscribe`.
 */
export class EventStreamClient {
  private readonly baseDelayMs: number;
  private readonly maxDelayMs: number;
  private readonly socketFactory: (url: string) => EventSocketLike;
  private readonly urlFactory: (streamId: string) => string;
  private readonly onStatus?: (status: EventClientStatus) => void;

  private readonly listeners = new Set<EventListener>();

  private streamId: string | null = null;
  private socket: EventSocketLike | null = null;
  private status: EventClientStatus = "idle";
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private intentionalClose = false;

  constructor(options: EventStreamClientOptions = {}) {
    this.baseDelayMs = options.baseDelayMs ?? 1_000;
    this.maxDelayMs = options.maxDelayMs ?? 15_000;
    this.socketFactory =
      options.socketFactory ??
      ((url) => new WebSocket(url) as unknown as EventSocketLike);
    this.urlFactory = options.urlFactory ?? buildEventWsUrl;
    this.onStatus = options.onStatus;
  }

  // -- subscription (independent of the connection) -----------------------

  /**
   * Register a listener for events and gaps. Returns a disposer for ergonomic
   * one-line cleanup; `unsubscribe(listener)` does the same thing.
   */
  subscribe(listener: EventListener): () => void {
    this.listeners.add(listener);
    return () => this.unsubscribe(listener);
  }

  /** Remove a previously registered listener. Idempotent. */
  unsubscribe(listener: EventListener): void {
    this.listeners.delete(listener);
  }

  // -- connection lifecycle ----------------------------------------------

  /**
   * Connect to `streamId`'s event stream. Idempotent for the same stream;
   * switching to a different stream tears the old socket down and reconnects.
   * Listeners are preserved across both.
   */
  connect(streamId: string): void {
    const switching = this.streamId !== null && this.streamId !== streamId;
    if (switching) {
      this.clearReconnectTimer();
      this.teardownSocket();
      this.attempt = 0;
    }
    this.streamId = streamId;
    this.intentionalClose = false;

    if (this.socket !== null) return; // already open/connecting
    if (this.reconnectTimer !== null && !switching) return; // reconnect pending
    this.openSocket();
  }

  /** Close the connection intentionally. Subscribers are kept. */
  disconnect(): void {
    this.intentionalClose = true;
    this.clearReconnectTimer();
    this.teardownSocket();
    this.attempt = 0;
    this.streamId = null;
    this.setStatus("closed");
  }

  getStatus(): EventClientStatus {
    return this.status;
  }

  // -- internals ----------------------------------------------------------

  private openSocket(): void {
    if (this.streamId === null) return;
    this.teardownSocket();
    this.setStatus(this.attempt === 0 ? "connecting" : "reconnecting");

    let socket: EventSocketLike;
    try {
      socket = this.socketFactory(this.urlFactory(this.streamId));
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.setStatus("open");
    };
    socket.onmessage = (event) => this.handleMessage(event);
    socket.onerror = () => {
      /* defer to onclose */
    };
    socket.onclose = () => {
      this.socket = null;
      if (this.intentionalClose) return;
      this.scheduleReconnect();
    };
  }

  private handleMessage(event: { data: unknown }): void {
    if (typeof event.data !== "string") return;

    let parsed: unknown;
    try {
      parsed = JSON.parse(event.data);
    } catch {
      return;
    }

    const notification = toNotification(parsed);
    if (notification === null) return;

    this.attempt = 0; // healthy traffic resets the backoff
    if (this.status !== "open") this.setStatus("open");
    this.emit(notification);
  }

  private emit(notification: EventNotification): void {
    // Snapshot so a listener may (un)subscribe during dispatch, and isolate
    // failures so one bad listener never starves the others.
    for (const listener of [...this.listeners]) {
      try {
        listener(notification);
      } catch {
        /* isolate misbehaving subscriber */
      }
    }
  }

  private scheduleReconnect(): void {
    this.clearReconnectTimer();
    this.setStatus("reconnecting");
    const delay = this.computeBackoff(this.attempt);
    this.attempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.openSocket();
    }, delay);
  }

  private computeBackoff(attempt: number): number {
    // Exponential growth, capped, with full jitter to avoid reconnect storms.
    const capped = Math.min(this.baseDelayMs * 2 ** attempt, this.maxDelayMs);
    return Math.random() * capped;
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private teardownSocket(): void {
    if (this.socket) {
      this.socket.onopen = null;
      this.socket.onmessage = null;
      this.socket.onerror = null;
      this.socket.onclose = null;
      try {
        this.socket.close();
      } catch {
        /* noop */
      }
      this.socket = null;
    }
  }

  private setStatus(status: EventClientStatus): void {
    if (this.status === status) return;
    this.status = status;
    try {
      this.onStatus?.(status);
    } catch {
      /* isolate a misbehaving status observer */
    }
  }
}

// --- shared singleton + bound free functions -------------------------------
// The app uses one event connection at a time; these wrap a process-wide
// client so callers can `import { connect, subscribe } from "@/services/events"`
// without threading an instance around. The class above remains exported for
// tests and for any future multi-stream need.

export const eventStreamClient = new EventStreamClient();

export function connect(streamId: string): void {
  eventStreamClient.connect(streamId);
}

export function disconnect(): void {
  eventStreamClient.disconnect();
}

export function subscribe(listener: EventListener): () => void {
  return eventStreamClient.subscribe(listener);
}

export function unsubscribe(listener: EventListener): void {
  eventStreamClient.unsubscribe(listener);
}
