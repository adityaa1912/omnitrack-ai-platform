import { StatusDot } from "@/components/ui/StatusDot";

/**
 * Top bar. The backend health indicator is wired to /health in a later
 * commit (needs the Query layer); for now it renders a static shell so the
 * layout is complete and stable.
 */
export function Topbar() {
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
        <StatusDot status="idle" />
        <span className="font-mono text-xs text-content-secondary">backend</span>
      </div>
    </header>
  );
}
