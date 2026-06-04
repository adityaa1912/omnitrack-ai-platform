import type { SocketStatus } from "@/lib/ws/StreamSocket";
import { StatusDot } from "./StatusDot";
import { cn } from "@/lib/utils/cn";

/**
 * Maps the connection-level SocketStatus to a compact badge. This reflects
 * the live socket, distinct from the server lifecycle (running/stopped).
 */
const MAP: Record<
  SocketStatus,
  { dot: "live" | "idle" | "warn" | "error"; label: string }
> = {
  idle: { dot: "idle", label: "idle" },
  connecting: { dot: "warn", label: "connecting" },
  open: { dot: "live", label: "live" },
  reconnecting: { dot: "warn", label: "reconnecting" },
  stale: { dot: "error", label: "stale" },
  closed: { dot: "idle", label: "closed" },
};

export function StreamStatusBadge({
  status,
  className,
}: {
  status: SocketStatus;
  className?: string;
}) {
  const { dot, label } = MAP[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-border-subtle bg-surface-200/60 px-2 py-0.5",
        className,
      )}
    >
      <StatusDot status={dot} />
      <span className="font-mono text-[10px] uppercase tracking-wide text-content-secondary">
        {label}
      </span>
    </span>
  );
}
