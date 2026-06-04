/**
 * Server-state hooks for stream lifecycle (TanStack Query).
 *
 * The streams list is the source of truth for stream EXISTENCE. Mutations
 * invalidate it so the UI reconciles after start/stop. Live frame state is
 * handled separately by the WS layer.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { streamsApi } from "@/lib/api/streams";
import type { StartStreamRequest } from "@/types/api";
import { queryKeys } from "./queryKeys";

export function useStreams() {
  return useQuery({
    queryKey: queryKeys.streams,
    queryFn: () => streamsApi.list(),
    refetchInterval: 4_000,
  });
}

export function useStartStream() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: StartStreamRequest) => streamsApi.start(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.streams }),
  });
}

export function useStopStream() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (streamId: string) => streamsApi.stop(streamId),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.streams }),
  });
}
