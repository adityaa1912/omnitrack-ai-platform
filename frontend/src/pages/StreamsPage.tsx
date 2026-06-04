import { Panel } from "@/components/ui/Panel";

/** Streams page placeholder. Stream management UI is added in later commits. */
export function StreamsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-content-primary">Streams</h1>
        <p className="text-sm text-content-muted">
          Manage inference stream lifecycle.
        </p>
      </div>
      <Panel className="flex h-64 items-center justify-center">
        <span className="font-mono text-sm text-content-muted">
          Stream management UI coming soon.
        </span>
      </Panel>
    </div>
  );
}
