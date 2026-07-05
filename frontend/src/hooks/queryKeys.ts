/** Centralized TanStack Query keys to avoid string drift across hooks. */
export const queryKeys = {
  health: ["health"] as const,
  streams: ["streams"] as const,
  streamMetrics: (streamId: string) => ["stream", streamId, "metrics"] as const,
  streamDetections: (streamId: string) =>
    ["stream", streamId, "detections"] as const,
  streamEvents: (streamId: string) => ["stream", streamId, "events"] as const,
  streamRegions: (streamId: string) => ["stream", streamId, "regions"] as const,
};
