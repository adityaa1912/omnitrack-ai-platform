import { AlertTriangle, Radio } from "lucide-react";
import { Panel } from "@/components/ui/Panel";
import { Spinner } from "@/components/ui/Spinner";
import { Button } from "@/components/ui/Button";

export function StreamGridLoading() {
  return (
    <Panel className="flex h-64 flex-col items-center justify-center gap-3">
      <Spinner className="h-6 w-6" />
      <span className="text-sm text-content-muted">Loading streams…</span>
    </Panel>
  );
}

export function StreamGridError({ onRetry }: { onRetry: () => void }) {
  return (
    <Panel className="flex h-64 flex-col items-center justify-center gap-3">
      <AlertTriangle className="h-6 w-6 text-status-error" />
      <span className="text-sm text-content-secondary">
        Could not reach the backend.
      </span>
      <Button variant="secondary" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </Panel>
  );
}

export function StreamGridEmpty({ onCreate }: { onCreate: () => void }) {
  return (
    <Panel className="flex h-64 flex-col items-center justify-center gap-3">
      <Radio className="h-6 w-6 text-surface-400" />
      <span className="text-sm text-content-secondary">No active streams.</span>
      <Button variant="primary" size="sm" onClick={onCreate}>
        Start a stream
      </Button>
    </Panel>
  );
}
