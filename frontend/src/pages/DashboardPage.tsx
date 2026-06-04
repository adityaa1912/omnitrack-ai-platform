import { Panel } from "@/components/ui/Panel";

/**
 * Overview page placeholder. Real-time stream grid and telemetry are added
 * in later commits once the WS service and stores exist.
 */
export function DashboardPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-content-primary">Overview</h1>
        <p className="text-sm text-content-muted">
          Platform status and active inference streams.
        </p>
      </div>
      <Panel className="flex h-64 items-center justify-center">
        <span className="font-mono text-sm text-content-muted">
          Stream grid &amp; telemetry land in the next commits.
        </span>
      </Panel>
    </div>
  );
}
