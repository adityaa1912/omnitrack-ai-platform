import { StreamViewer } from "@/components/viewer/StreamViewer";

/**
 * Live frame viewer host. Maintains the 16:9 container so card layout is
 * stable; the imperative StreamViewer renders frames into it.
 */
export function StreamThumbnail({ streamId }: { streamId: string }) {
  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-lg border border-border-subtle bg-surface-0">
      <StreamViewer streamId={streamId} />
    </div>
  );
}
