import { StreamGrid } from "@/components/streams/StreamGrid";

/**
 * Overview page. Renders the live stream grid. Aggregate telemetry charts
 * are added in a later commit.
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
      <StreamGrid />
    </div>
  );
}
