import { Video } from "lucide-react";

/**
 * Placeholder slot for the live frame viewer. The commit 4 viewer will
 * replace the inner content; the aspect-ratio container and styling stay so
 * the card layout is stable across commits.
 */
export function StreamThumbnail() {
  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-lg border border-border-subtle bg-surface-0">
      <div className="absolute inset-0 flex items-center justify-center">
        <Video className="h-6 w-6 text-surface-400" />
      </div>
      <div
        className="absolute inset-0 opacity-40"
        style={{
          backgroundImage:
            "linear-gradient(0deg, rgba(255,255,255,0.02) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px)",
          backgroundSize: "24px 24px",
        }}
        aria-hidden="true"
      />
    </div>
  );
}
