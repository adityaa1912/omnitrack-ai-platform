import { Activity } from "lucide-react";
import { Panel } from "@/components/ui/Panel";

export function TelemetryEmptyState() {
  return (
    <Panel className="flex h-40 flex-col items-center justify-center gap-2">
      <Activity className="h-5 w-5 text-surface-400" />
      <span className="text-sm text-content-secondary">
        No telemetry yet.
      </span>
      <span className="text-xs text-content-muted">
        Start a stream to begin collecting operational metrics.
      </span>
    </Panel>
  );
}
