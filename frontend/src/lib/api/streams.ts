/**
 * Endpoint functions for the OmniTrack backend.
 *
 * One function per backend route, typed against types/api.ts. Note that
 * POST /stream/stop takes stream_id as a QUERY parameter (not a body),
 * matching backend/main.py exactly.
 */

import { apiClient } from "./client";
import type {
  Detection,
  HealthResponse,
  LineSpec,
  RegionsResponse,
  StartStreamRequest,
  StreamMetrics,
  StreamSummary,
  ZoneSpec,
} from "@/types/api";
import type { StreamEvent } from "@/services/events";

/** Body for a live regions update (PUT /stream/{id}/regions). */
export interface RegionsUpdate {
  zones: ZoneSpec[];
  lines: LineSpec[];
  dwell_seconds: number;
}

export const streamsApi = {
  health: () => apiClient.get<HealthResponse>("/health"),

  list: () => apiClient.get<StreamSummary[]>("/streams"),

  start: (payload: StartStreamRequest) =>
    apiClient.post<StreamMetrics>("/stream/start", { body: payload }),

  // stream_id is a query parameter on the backend.
  stop: (streamId: string) =>
    apiClient.post<{ status: string; stream_id: string }>("/stream/stop", {
      query: { stream_id: streamId },
    }),

  metrics: (streamId: string) =>
    apiClient.get<StreamMetrics>(`/stream/${encodeURIComponent(streamId)}/metrics`),

  detections: (streamId: string) =>
    apiClient.get<Detection[]>(`/stream/${encodeURIComponent(streamId)}/detections`),

  // Newest-first event history from the in-memory event store. `limit` is
  // bounded server-side (1..1000, default 100); omitted here to take the
  // server default. Used to backfill the live feed on mount.
  events: (streamId: string, limit?: number) =>
    apiClient.get<StreamEvent[]>(
      `/stream/${encodeURIComponent(streamId)}/events`,
      limit != null ? { query: { limit } } : undefined,
    ),

  // Scene-region geometry configured at stream start (zones/lines + the source
  // frame dimensions the coordinates live in).
  regions: (streamId: string) =>
    apiClient.get<RegionsResponse>(`/stream/${encodeURIComponent(streamId)}/regions`),

  // Replace a running stream's scene regions live (no restart).
  updateRegions: (streamId: string, body: RegionsUpdate) =>
    apiClient.put<RegionsResponse>(
      `/stream/${encodeURIComponent(streamId)}/regions`,
      { body },
    ),
};
