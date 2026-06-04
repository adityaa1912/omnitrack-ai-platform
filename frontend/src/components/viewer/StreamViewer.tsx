import { useEffect, useRef } from "react";
import { useStreamStore } from "@/store/useStreamStore";
import type { SocketStatus } from "@/lib/ws/StreamSocket";
import { CanvasFrameRenderer } from "@/lib/render/CanvasFrameRenderer";
import { decodeJpegBase64 } from "@/lib/render/decodeFrame";
import { cn } from "@/lib/utils/cn";

/**
 * Imperative live frame viewer.
 *
 * Renders ONCE. Per-frame work is entirely outside React:
 *  - subscribes transiently to the store (no component rerender)
 *  - decodes the latest frame to an ImageBitmap and submits it to the
 *    CanvasFrameRenderer, which owns the rAF loop and disposal
 *  - a monotonic seq token drops out-of-order decodes
 *
 * A separate narrow selector drives only the stale/reconnecting overlay,
 * composited over the last good frame.
 */
export function StreamViewer({ streamId }: { streamId: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const renderer = new CanvasFrameRenderer(canvas);
    let disposed = false;
    let seq = 0;
    let lastTimestamp = -1;

    const handleFrame = (frameBase64: string, detections: Parameters<typeof renderer.submit>[1], ts: number) => {
      if (disposed || ts === lastTimestamp) return;
      lastTimestamp = ts;
      const mySeq = (seq += 1);
      void decodeJpegBase64(frameBase64, () => disposed).then((bitmap) => {
        if (!bitmap) return;
        renderer.submit(bitmap, detections, mySeq);
      });
    };

    // Transient subscription: fires on store changes WITHOUT rerendering.
    const unsubscribe = useStreamStore.subscribe((state) => {
      const runtime = state.streams[streamId];
      const frame = runtime?.latestFrame;
      if (!frame) return;
      handleFrame(frame.frame, frame.detections, frame.timestamp);
    });

    // Draw the frame that may already be present at mount.
    const initial = useStreamStore.getState().streams[streamId]?.latestFrame;
    if (initial) handleFrame(initial.frame, initial.detections, initial.timestamp);

    return () => {
      disposed = true;
      unsubscribe();
      renderer.dispose();
    };
  }, [streamId]);

  return (
    <div className="relative h-full w-full">
      <canvas ref={canvasRef} className="h-full w-full" />
      <ViewerOverlay streamId={streamId} />
    </div>
  );
}

/**
 * Status overlay. Subscribes to the narrow status slice only, so it rerenders
 * on connection-state transitions (rare) and never per frame.
 */
function ViewerOverlay({ streamId }: { streamId: string }) {
  const status: SocketStatus =
    useStreamStore((s) => s.streams[streamId]?.status) ?? "idle";
  const hasFrame = useStreamStore((s) => Boolean(s.streams[streamId]?.latestFrame));

  const degraded = status === "stale" || status === "reconnecting";
  const connecting = status === "connecting";

  if (!hasFrame) {
    return (
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="font-mono text-[10px] uppercase tracking-widest text-content-muted">
          {connecting ? "connecting" : status === "open" ? "awaiting frames" : "no signal"}
        </span>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "pointer-events-none absolute inset-0 transition-opacity duration-300",
        degraded ? "opacity-100" : "opacity-0",
      )}
    >
      <div className="absolute inset-0 bg-surface-0/45" />
      <div className="absolute bottom-2 left-2 flex items-center gap-1.5 rounded-md border border-border-subtle bg-surface-0/80 px-2 py-1">
        <span className="h-1.5 w-1.5 animate-pulse-live rounded-full bg-status-warn" />
        <span className="font-mono text-[10px] uppercase tracking-wide text-content-secondary">
          {status === "stale" ? "signal lost" : "reconnecting"}
        </span>
      </div>
    </div>
  );
}
