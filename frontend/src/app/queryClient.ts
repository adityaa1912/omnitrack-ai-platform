import { QueryClient } from "@tanstack/react-query";

/**
 * Shared TanStack Query client. Tuned for an operations dashboard:
 * short stale time (data is live), bounded retries, and no refetch storms
 * on window focus (polling is configured per-query in later commits).
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 2_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});
