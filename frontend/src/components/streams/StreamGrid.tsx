import { useState } from "react";
import { Plus } from "lucide-react";
import { useStreams } from "@/hooks/useStreams";
import { Button } from "@/components/ui/Button";
import { StreamCard } from "./StreamCard";
import { CreateStreamDialog } from "./CreateStreamDialog";
import {
  StreamGridEmpty,
  StreamGridError,
  StreamGridLoading,
} from "./StreamGridStates";

/**
 * Owner of the streams list query and lifecycle reconciliation. Renders one
 * keyed StreamCard per backend stream; when a stream disappears from the
 * list the card unmounts and its socket is disposed automatically.
 */
export function StreamGrid() {
  const { data, isLoading, isError, refetch } = useStreams();
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-content-muted">
          {data ? `${data.length} stream${data.length === 1 ? "" : "s"}` : "\u00a0"}
        </span>
        <Button variant="primary" size="sm" onClick={() => setDialogOpen(true)}>
          <Plus className="h-4 w-4" />
          New stream
        </Button>
      </div>

      {isLoading ? (
        <StreamGridLoading />
      ) : isError ? (
        <StreamGridError onRetry={() => refetch()} />
      ) : !data || data.length === 0 ? (
        <StreamGridEmpty onCreate={() => setDialogOpen(true)} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {data.map((stream) => (
            <StreamCard key={stream.stream_id} stream={stream} />
          ))}
        </div>
      )}

      <CreateStreamDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />
    </div>
  );
}
