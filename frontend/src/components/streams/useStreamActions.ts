/**
 * Thin orchestration over the start/stop mutations. Keeps mutation wiring
 * out of presentational components and centralizes invalidation behavior.
 */

import { useStartStream, useStopStream } from "@/hooks/useStreams";

export function useStreamActions() {
  const start = useStartStream();
  const stop = useStopStream();
  return { start, stop };
}
