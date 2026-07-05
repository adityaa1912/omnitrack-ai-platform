/**
 * Framework-agnostic real-time stream socket.
 *
 * One instance per stream_id. Owns a native WebSocket and implements:
 *  - an explicit lifecycle state machine
 *  - exponential backoff + full jitter reconnect (storm-safe)
 *  - message-driven heartbeat (frame AND keep_alive count as liveness)
 *  - a stale-connection watchdog that force-reconnects half-open sockets
 *  - latest-frame-only backpressure (never queues frames)
 *
 * No React here by design: this is independently testable transport logic.
 */

import { env } from "@/config/env";
import type { WsFrameMessage, WsMessage } from "@/types/api";

export type SocketStatus =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "stale"
  | "closed";

export interface StreamSocketOptions {
  baseDelayMs?: number;
  maxDelayMs?: number;
  staleTimeoutMs?: number;
  onFrame?: (frame: WsFrameMessage) => void;
  onStatus?: (status: SocketStatus) => void;
}

function buildWsUrl(streamId: string): string {
  const base = env.wsBaseUrl.replace(/\/$/, "");
  const path = `/stream/${encodeURIComponent(streamId)}/ws`;
  const query = env.apiKey ? `?api_key=${encodeURIComponent(env.apiKey)}` : "";
  if (/^wss?:\/\//i.test(base)) return `${base}${path}${query}`;
  const origin = window.location.origin.replace(/^http/i, "ws");
  return `${origin}${base}${path}${query}`;
}

function isWsMessage(value: unknown): value is WsMessage {
  if (typeof value !== "object" || value === null) return false;
  const type = (value as { type?: unknown }).type;
  return type === "frame" || type === "keep_alive";
}

export class StreamSocket {
  private readonly streamId: string;
  private readonly baseDelayMs: number;
  private readonly maxDelayMs: number;
  private readonly staleTimeoutMs: number;
  private readonly onFrame?: (frame: WsFrameMessage) => void;
  private readonly onStatus?: (status: SocketStatus) => void;

  private ws: WebSocket | null = null;
  private status: SocketStatus = "idle";
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private staleTimer: ReturnType<typeof setTimeout> | null = null;
  private intentionalClose = false;

  constructor(streamId: string, options: StreamSocketOptions = {}) {
    this.streamId = streamId;
    this.baseDelayMs = options.baseDelayMs ?? 1_000;
    this.maxDelayMs = options.maxDelayMs ?? 15_000;
    this.staleTimeoutMs = options.staleTimeoutMs ?? 8_000;
    this.onFrame = options.onFrame;
    this.onStatus = options.onStatus;
  }

  getStatus(): SocketStatus {
    return this.status;
  }

  connect(): void {
    this.intentionalClose = false;
    this.openSocket();
  }

  close(): void {
    this.intentionalClose = true;
    this.clearReconnectTimer();
    this.clearStaleTimer();
    this.teardownSocket();
    this.setStatus("closed");
  }

  private openSocket(): void {
    this.teardownSocket();
    this.setStatus(this.attempt === 0 ? "connecting" : "reconnecting");

    let ws: WebSocket;
    try {
      ws = new WebSocket(buildWsUrl(this.streamId));
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      this.setStatus("open");
      this.armStaleTimer();
    };
    ws.onmessage = (event) => this.handleMessage(event);
    ws.onerror = () => {
      /* defer to onclose */
    };
    ws.onclose = () => {
      this.clearStaleTimer();
      if (this.intentionalClose) return;
      this.scheduleReconnect();
    };
  }

  private handleMessage(event: MessageEvent): void {
    this.armStaleTimer();
    if (typeof event.data !== "string") return;

    let parsed: unknown;
    try {
      parsed = JSON.parse(event.data);
    } catch {
      return;
    }
    if (!isWsMessage(parsed)) return;
    if (parsed.type === "keep_alive") return;

    this.attempt = 0;
    if (this.status !== "open") this.setStatus("open");
    this.onFrame?.(parsed);
  }

  private scheduleReconnect(): void {
    this.clearReconnectTimer();
    this.setStatus("reconnecting");
    const delay = this.computeBackoff(this.attempt);
    this.attempt += 1;
    this.reconnectTimer = setTimeout(() => this.openSocket(), delay);
  }

  private computeBackoff(attempt: number): number {
    const capped = Math.min(this.baseDelayMs * 2 ** attempt, this.maxDelayMs);
    return Math.random() * capped;
  }

  private armStaleTimer(): void {
    this.clearStaleTimer();
    this.staleTimer = setTimeout(() => {
      this.setStatus("stale");
      this.teardownSocket();
      if (!this.intentionalClose) this.scheduleReconnect();
    }, this.staleTimeoutMs);
  }

  private clearStaleTimer(): void {
    if (this.staleTimer !== null) {
      clearTimeout(this.staleTimer);
      this.staleTimer = null;
    }
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private teardownSocket(): void {
    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.onclose = null;
      try {
        this.ws.close();
      } catch {
        /* noop */
      }
      this.ws = null;
    }
  }

  private setStatus(status: SocketStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.onStatus?.(status);
  }
}
