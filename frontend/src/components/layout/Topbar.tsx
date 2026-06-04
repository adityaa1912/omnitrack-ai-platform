import { StatusDot } from "@/components/ui/StatusDot";
import { useHealth } from "@/hooks/useHealth";

/** Top bar with a live backend health indicator driven by /health. */
export function Topbar() {
  const { data, isError, isLoading } = useHealth();

  const status = isError
    ? "error"
    : isLoading
      ? "idle"
      : data?.status === "healthy"
        ? "live"
        : "warn";

  const label = isError ? "offline" : data ? `${data.active_streams} active` : "connecting";

  return (
    <header className="flex h-16 shrink-0 items-center justify-between border-b border-border-subtle bg-surface-50/40 px-6 backdrop-blur-sm">
      <div className="flex flex-col">
        <span className="text-sm font-medium text-content-primary">
          Real-time AI Operations
        </span>
        <span className="text-xs text-content-muted">
          Live inference, tracking &amp; telemetry
        </span>
      </div>
      <div className="flex items-center gap-2 rounded-full border border-border-subtle bg-surface-100 px-3 py-1.5">
        <StatusDot status={status} />
        <span className="font-mono text-xs text-content-secondary">backend</span>
        <span className="font-mono text-xs text-content-muted">{label}</span>
      </div>
    </header>
  );
}
