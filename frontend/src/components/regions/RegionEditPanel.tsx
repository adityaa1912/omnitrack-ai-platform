import { useState } from "react";
import { Pencil, X } from "lucide-react";
import type { LineSpec, RegionsResponse, ZoneSpec } from "@/types/api";
import { useUpdateRegions } from "@/hooks/useStreamRegions";
import { ZoneEditor } from "@/components/regions/ZoneEditor";
import { Panel } from "@/components/ui/Panel";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { TextField } from "@/components/ui/TextField";
import { ApiError } from "@/lib/api/client";

/**
 * Live scene-region editor for a running stream. Seeds the shared ZoneEditor
 * from the stream's current regions, then PUTs the edited set — the event
 * engine's geometry detectors are rebuilt in place (no restart, existing tracks
 * preserved). Draws on a source-dimension canvas (same as the create dialog),
 * not interactively over the video, so coordinates map exactly.
 *
 * Requires the current regions (for the source dimensions + starting geometry);
 * renders nothing until they load.
 */
export function RegionEditPanel({
  streamId,
  regions,
  className,
}: {
  streamId: string;
  regions: RegionsResponse | undefined;
  className?: string;
}) {
  const [open, setOpen] = useState(false);

  if (!regions) return null;

  return (
    <Panel className={className}>
      <div className="flex items-center justify-between px-4 py-3">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-content-primary">Scene regions</h3>
          <span className="font-mono text-[10px] text-content-muted">
            {regions.zones.length} zone{regions.zones.length === 1 ? "" : "s"},{" "}
            {regions.lines.length} line{regions.lines.length === 1 ? "" : "s"}
          </span>
        </div>
        <Button size="sm" variant={open ? "ghost" : "secondary"} onClick={() => setOpen((v) => !v)}>
          {open ? <X className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}
          {open ? "Close" : "Edit"}
        </Button>
      </div>

      {open && (
        <div className="border-t border-border-subtle p-4">
          <RegionEditForm
            streamId={streamId}
            regions={regions}
            onDone={() => setOpen(false)}
          />
        </div>
      )}
    </Panel>
  );
}

function RegionEditForm({
  streamId,
  regions,
  onDone,
}: {
  streamId: string;
  regions: RegionsResponse;
  onDone: () => void;
}) {
  // Seed local editable state from the current server regions.
  const [zones, setZones] = useState<ZoneSpec[]>(regions.zones);
  const [lines, setLines] = useState<LineSpec[]>(regions.lines);
  const [dwell, setDwell] = useState(String(regions.dwell_seconds));
  const [error, setError] = useState<string | null>(null);

  const update = useUpdateRegions(streamId);

  const handleSave = () => {
    setError(null);
    const dwellNum = Number(dwell);
    if (Number.isNaN(dwellNum) || dwellNum <= 0) {
      setError("Dwell seconds must be greater than 0.");
      return;
    }
    update.mutate(
      { zones, lines, dwell_seconds: dwellNum },
      {
        onSuccess: () => onDone(),
        onError: (err) =>
          setError(err instanceof ApiError ? err.message : "Failed to update regions."),
      },
    );
  };

  return (
    <div className="flex flex-col gap-3">
      <ZoneEditor
        width={regions.width}
        height={regions.height}
        zones={zones}
        lines={lines}
        onChange={({ zones: z, lines: l }) => {
          setZones(z);
          setLines(l);
        }}
      />
      <TextField
        label="Dwell seconds"
        value={dwell}
        onChange={(e) => setDwell(e.target.value)}
        inputMode="decimal"
      />
      {error && <p className="text-xs text-status-error">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onDone} disabled={update.isPending}>
          Cancel
        </Button>
        <Button variant="primary" size="sm" onClick={handleSave} disabled={update.isPending}>
          {update.isPending ? <Spinner className="h-3.5 w-3.5" /> : null}
          {update.isPending ? "Applying" : "Apply live"}
        </Button>
      </div>
    </div>
  );
}
