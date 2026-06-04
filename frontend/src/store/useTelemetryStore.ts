/**
 * Telemetry store (Zustand).
 *
 * Fixed-length ring buffers of metric samples per stream. Bounded memory by
 * construction. Populated by the metrics hook; consumed by charts later.
 * Separate from the frame store so telemetry updates never rerender frame
 * consumers and vice versa.
 */

import { create } from "zustand";

export interface TelemetrySample {
  t: number;
  fps: number;
  inferenceMs: number;
  totalDetections: number;
  totalFrames: number;
}

const MAX_SAMPLES = 120;

interface TelemetryStoreState {
  series: Record<string, TelemetrySample[]>;
  pushSample: (streamId: string, sample: TelemetrySample) => void;
  clear: (streamId: string) => void;
}

export const useTelemetryStore = create<TelemetryStoreState>((set) => ({
  series: {},

  pushSample: (streamId, sample) =>
    set((state) => {
      const prev = state.series[streamId] ?? [];
      const next = prev.length >= MAX_SAMPLES ? prev.slice(1) : prev.slice();
      next.push(sample);
      return { series: { ...state.series, [streamId]: next } };
    }),

  clear: (streamId) =>
    set((state) => {
      if (!(streamId in state.series)) return state;
      const next = { ...state.series };
      delete next[streamId];
      return { series: next };
    }),
}));

/**
 * Shared stable empty array. Returning a fresh `[]` per selector call would
 * create a new reference every render and defeat reference-based memoization
 * downstream, causing needless chart rerenders. A single constant keeps the
 * "no samples yet" case referentially stable.
 */
const EMPTY_SERIES: readonly TelemetrySample[] = Object.freeze([]);

export const selectTelemetry =
  (streamId: string) =>
  (state: TelemetryStoreState): readonly TelemetrySample[] =>
    state.series[streamId] ?? EMPTY_SERIES;
