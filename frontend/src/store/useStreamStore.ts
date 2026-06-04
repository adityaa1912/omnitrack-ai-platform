/**
 * Real-time stream store (Zustand).
 *
 * Holds ONLY ephemeral live state that does not belong in TanStack Query:
 *  - per-stream socket connection status
 *  - the single latest frame per stream (backpressure: never an array)
 *
 * Keyed by stream_id so a frame update for one stream never rerenders
 * components subscribed to another stream. Read narrow slices.
 */

import { create } from "zustand";
import type { WsFrameMessage } from "@/types/api";
import type { SocketStatus } from "@/lib/ws/StreamSocket";

interface StreamRuntime {
  status: SocketStatus;
  latestFrame: WsFrameMessage | null;
  lastFrameAt: number | null;
}

interface StreamStoreState {
  streams: Record<string, StreamRuntime>;
  setStatus: (streamId: string, status: SocketStatus) => void;
  setFrame: (streamId: string, frame: WsFrameMessage) => void;
  removeStream: (streamId: string) => void;
}

const EMPTY_RUNTIME: StreamRuntime = {
  status: "idle",
  latestFrame: null,
  lastFrameAt: null,
};

export const useStreamStore = create<StreamStoreState>((set) => ({
  streams: {},

  setStatus: (streamId, status) =>
    set((state) => {
      const prev = state.streams[streamId] ?? EMPTY_RUNTIME;
      if (prev.status === status) return state;
      return { streams: { ...state.streams, [streamId]: { ...prev, status } } };
    }),

  setFrame: (streamId, frame) =>
    set((state) => {
      const prev = state.streams[streamId] ?? EMPTY_RUNTIME;
      return {
        streams: {
          ...state.streams,
          [streamId]: { ...prev, latestFrame: frame, lastFrameAt: Date.now() },
        },
      };
    }),

  removeStream: (streamId) =>
    set((state) => {
      if (!(streamId in state.streams)) return state;
      const next = { ...state.streams };
      delete next[streamId];
      return { streams: next };
    }),
}));

export const selectStreamRuntime =
  (streamId: string) => (state: StreamStoreState) =>
    state.streams[streamId];
