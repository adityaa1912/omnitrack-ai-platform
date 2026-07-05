/**
 * Fetches the scene-region geometry configured on a stream (GET /regions).
 *
 * Regions are fixed at stream start, so this is effectively static for a
 * stream's lifetime — a long staleTime avoids needless refetches. Used to draw
 * the zone/line overlay on the viewer.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { streamsApi, type RegionsUpdate } from "@/lib/api/streams";
import { queryKeys } from "./queryKeys";

export function useStreamRegions(streamId: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.streamRegions(streamId),
    queryFn: () => streamsApi.regions(streamId),
    enabled: enabled && Boolean(streamId),
    staleTime: 30_000,
  });
}

/**
 * Live-reconfigure a running stream's regions. On success the regions query is
 * updated with the server's response, so the viewer overlay (which reads the
 * same query) reflects the change immediately without a restart.
 */
export function useUpdateRegions(streamId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RegionsUpdate) => streamsApi.updateRegions(streamId, body),
    onSuccess: (data) => {
      qc.setQueryData(queryKeys.streamRegions(streamId), data);
    },
  });
}
