import { useQuery } from "@tanstack/react-query";
import { streamsApi } from "@/lib/api/streams";
import { queryKeys } from "./queryKeys";

/** Live backend health, polled. Drives the Topbar status indicator. */
export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => streamsApi.health(),
    refetchInterval: 5_000,
    staleTime: 4_000,
  });
}
