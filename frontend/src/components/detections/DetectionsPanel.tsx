import { Boxes } from "lucide-react";
import type { Detection } from "@/types/api";
import { useStreamDetections } from "@/hooks/useStreamDetections";
import { Panel } from "@/components/ui/Panel";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/utils/cn";

/**
 * Textual list of a stream's currently tracked objects (track id, class,
 * confidence). A ~1Hz snapshot that complements the per-frame boxes drawn in
 * the viewer — useful for reading exact track ids and confidences. Purely a
 * consumer of the detections query; never touches the frame render path.
 */
export function DetectionsPanel({
  streamId,
  enabled = true,
  className,
}: {
  streamId: string;
  enabled?: boolean;
  className?: string;
}) {
  const { data, isLoading } = useStreamDetections(streamId, enabled);
  const detections = data ?? [];

  return (
    <Panel className={cn("flex flex-col overflow-hidden", className)}>
      <header className="flex items-center justify-between gap-3 border-b border-border-subtle px-4 py-3">
        <div className="flex items-center gap-2">
          <Boxes className="h-4 w-4 text-content-muted" />
          <h3 className="text-sm font-semibold text-content-primary">Detections</h3>
          <span className="font-mono text-[10px] text-content-muted">
            {detections.length}
          </span>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {isLoading && detections.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-8 text-content-muted">
            <Spinner className="h-4 w-4" />
            <span className="text-xs">Loading…</span>
          </div>
        ) : detections.length === 0 ? (
          <div className="flex items-center justify-center py-8">
            <span className="font-mono text-[10px] uppercase tracking-widest text-content-muted">
              no objects detected
            </span>
          </div>
        ) : (
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-surface-100/90 text-[10px] uppercase tracking-wide text-content-muted">
              <tr>
                <th className="px-4 py-1.5 font-medium">Track</th>
                <th className="px-4 py-1.5 font-medium">Class</th>
                <th className="px-4 py-1.5 text-right font-medium">Conf.</th>
              </tr>
            </thead>
            <tbody>
              {detections.map((det, i) => (
                <DetectionRow key={det.track_id ?? `idx-${i}`} detection={det} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}

function DetectionRow({ detection }: { detection: Detection }) {
  return (
    <tr className="border-t border-border-subtle/60">
      <td className="px-4 py-1.5 font-mono text-content-secondary">
        {detection.track_id ?? "—"}
      </td>
      <td className="px-4 py-1.5 text-content-primary">{detection.class_name}</td>
      <td className="px-4 py-1.5 text-right font-mono tabular-nums text-content-secondary">
        {(detection.confidence * 100).toFixed(0)}%
      </td>
    </tr>
  );
}
