import { StreamGrid } from "@/components/streams/StreamGrid";

export function StreamsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold text-content-primary">Streams</h1>
        <p className="text-sm text-content-muted">
          Manage inference stream lifecycle.
        </p>
      </div>
      <StreamGrid />
    </div>
  );
}
