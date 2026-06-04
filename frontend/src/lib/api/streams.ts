/**
 * Endpoint functions for the OmniTrack backend.
 *
 * One function per backend route, typed against types/api.ts. Note that
 * POST /stream/stop takes stream_id as a QUERY parameter (not a body),
 * matching backend/app.py exactly.
 */

import { apiClient } from "./client";
import type {
  Detection,
  HealthResponse,
  StartStreamRequest,
  StreamMetrics,
  StreamSummary,
} from "@/types/api";

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
};
