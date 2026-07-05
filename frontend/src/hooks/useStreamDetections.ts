/**
 * Polls a stream's latest tracked detections (a current-frame snapshot from
 * GET /stream/{id}/detections).
 *
 * This is read-only server state, so it lives in React Query (not the frame
 * store). It is intentionally separate from the frame WebSocket: the viewer
 * draws boxes per frame off the socket, while this endpoint backs a textual
 * list at a calm ~1Hz cadence. Enable only while the stream is running and in
 * view so we never poll idle streams.
 */

import { useQuery } from "@tanstack/react-query";
import { streamsApi } from "@/lib/api/streams";
import { queryKeys } from "./queryKeys";

export function useStreamDetections(streamId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.streamDetections(streamId),
    queryFn: () => streamsApi.detections(streamId),
    enabled: enabled && Boolean(streamId),
    refetchInterval: 1_000,
  });
}
