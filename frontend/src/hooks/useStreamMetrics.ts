/**
 * Polls a single stream's metrics and appends a telemetry sample to the
 * ring buffer. Latest metrics live in Query; rolling history lives in the
 * telemetry store. Enable only for streams in view.
 */

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { streamsApi } from "@/lib/api/streams";
import { useTelemetryStore } from "@/store/useTelemetryStore";
import { queryKeys } from "./queryKeys";

export function useStreamMetrics(streamId: string, enabled = true) {
  const pushSample = useTelemetryStore((s) => s.pushSample);

  const query = useQuery({
    queryKey: queryKeys.streamMetrics(streamId),
    queryFn: () => streamsApi.metrics(streamId),
    enabled,
    refetchInterval: 1_000,
  });

  const data = query.data;
  useEffect(() => {
    if (!data) return;
    pushSample(streamId, {
      t: Date.now(),
      fps: data.fps,
      inferenceMs: data.inference_time_avg_ms,
      totalDetections: data.total_detections,
      totalFrames: data.total_frames,
    });
  }, [data, streamId, pushSample]);

  return query;
}
