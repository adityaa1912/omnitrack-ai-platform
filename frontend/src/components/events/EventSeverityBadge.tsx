import { severityStyle } from "@/lib/events/eventMeta";
import { cn } from "@/lib/utils/cn";

/**
 * Compact severity pill for an event. Colour + label come from the shared
 * severity mapping so the whole app renders severities identically.
 */
export function EventSeverityBadge({
  severity,
  className,
}: {
  severity: string;
  className?: string;
}) {
  const style = severityStyle(severity);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5",
        style.badgeClass,
        className,
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", style.dotClass)} />
      <span className="font-mono text-[10px] uppercase tracking-wide">
        {style.label}
      </span>
    </span>
  );
}
