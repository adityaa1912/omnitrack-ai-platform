import { useState } from "react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/TextField";
import { Toggle } from "@/components/ui/Toggle";
import { Spinner } from "@/components/ui/Spinner";
import { useStartStream } from "@/hooks/useStreams";
import { ApiError } from "@/lib/api/client";
import { ZoneEditor } from "@/components/regions/ZoneEditor";
import type { LineSpec, StartStreamRequest, ZoneSpec } from "@/types/api";

/**
 * Start-stream form. On success the list query is invalidated and the new
 * card appears via reconciliation (we do not optimistically inject a fake
 * stream, since the server assigns real metrics).
 */
export function CreateStreamDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const start = useStartStream();
  const [streamId, setStreamId] = useState("");
  const [source, setSource] = useState("0");
  const [tracking, setTracking] = useState(true);
  const [confidence, setConfidence] = useState("0.5");
  const [width, setWidth] = useState("640");
  const [height, setHeight] = useState("480");
  const [dwell, setDwell] = useState("5");
  const [zones, setZones] = useState<ZoneSpec[]>([]);
  const [lines, setLines] = useState<LineSpec[]>([]);
  const [showRegions, setShowRegions] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const reset = () => {
    setStreamId("");
    setSource("0");
    setTracking(true);
    setConfidence("0.5");
    setWidth("640");
    setHeight("480");
    setDwell("5");
    setZones([]);
    setLines([]);
    setShowRegions(false);
    setFormError(null);
  };

  const handleClose = () => {
    if (start.isPending) return;
    reset();
    onClose();
  };

  const handleSubmit = () => {
    setFormError(null);
    const id = streamId.trim();
    if (!id) {
      setFormError("Stream ID is required.");
      return;
    }
    const conf = Number(confidence);
    if (Number.isNaN(conf) || conf < 0 || conf > 1) {
      setFormError("Confidence must be between 0 and 1.");
      return;
    }
    const w = Number(width);
    const h = Number(height);
    if (!Number.isInteger(w) || w <= 0 || !Number.isInteger(h) || h <= 0) {
      setFormError("Width and height must be positive integers.");
      return;
    }
    const dwellNum = Number(dwell);
    if (Number.isNaN(dwellNum) || dwellNum <= 0) {
      setFormError("Dwell seconds must be greater than 0.");
      return;
    }

    // Webcam index is numeric; file paths / RTSP URLs are strings.
    const parsedSource: number | string = /^\d+$/.test(source.trim())
      ? Number(source.trim())
      : source.trim();

    // Geometry only has effect with tracking on (events derive from tracks).
    const hasGeometry = tracking && (zones.length > 0 || lines.length > 0);
    const payload: StartStreamRequest = {
      stream_id: id,
      source: parsedSource,
      tracking_enabled: tracking,
      confidence_threshold: conf,
      width: w,
      height: h,
      ...(tracking && zones.length > 0 ? { zones } : {}),
      ...(tracking && lines.length > 0 ? { lines } : {}),
      ...(hasGeometry ? { dwell_seconds: dwellNum } : {}),
    };

    start.mutate(payload, {
      onSuccess: () => {
        reset();
        onClose();
      },
      onError: (err) => {
        setFormError(
          err instanceof ApiError ? err.message : "Failed to start stream.",
        );
      },
    });
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      title="Start inference stream"
      description="Provide a source (0 for webcam, a file path, or an RTSP URL)."
    >
      <div className="flex max-h-[70vh] flex-col gap-4 overflow-y-auto pr-1">
        <TextField
          label="Stream ID"
          placeholder="e.g. lobby-cam"
          value={streamId}
          onChange={(e) => setStreamId(e.target.value)}
          autoComplete="off"
        />
        <TextField
          label="Source"
          hint="0 = default webcam"
          value={source}
          onChange={(e) => setSource(e.target.value)}
          autoComplete="off"
        />
        <div className="grid grid-cols-2 gap-3">
          <TextField
            label="Width"
            value={width}
            onChange={(e) => setWidth(e.target.value)}
            inputMode="numeric"
          />
          <TextField
            label="Height"
            value={height}
            onChange={(e) => setHeight(e.target.value)}
            inputMode="numeric"
          />
        </div>
        <TextField
          label="Confidence threshold"
          value={confidence}
          onChange={(e) => setConfidence(e.target.value)}
          inputMode="decimal"
        />
        <Toggle label="Object tracking" checked={tracking} onChange={setTracking} />

        {tracking && (
          <div className="rounded-lg border border-border-subtle p-3">
            <button
              type="button"
              onClick={() => setShowRegions((v) => !v)}
              className="flex w-full items-center justify-between text-xs font-medium text-content-secondary"
            >
              <span>Scene regions (optional)</span>
              <span className="font-mono text-content-muted">
                {zones.length + lines.length > 0
                  ? `${zones.length} zone${zones.length === 1 ? "" : "s"}, ${lines.length} line${lines.length === 1 ? "" : "s"}`
                  : showRegions
                    ? "hide"
                    : "add"}
              </span>
            </button>
            {showRegions && (
              <div className="mt-3 flex flex-col gap-3">
                <ZoneEditor
                  width={Number(width) > 0 ? Number(width) : 640}
                  height={Number(height) > 0 ? Number(height) : 480}
                  zones={zones}
                  lines={lines}
                  onChange={({ zones: z, lines: l }) => {
                    setZones(z);
                    setLines(l);
                  }}
                />
                <TextField
                  label="Dwell seconds"
                  hint="continuous time in a zone before a dwell event"
                  value={dwell}
                  onChange={(e) => setDwell(e.target.value)}
                  inputMode="decimal"
                />
              </div>
            )}
          </div>
        )}

        {formError ? (
          <p className="text-xs text-status-error">{formError}</p>
        ) : null}

        <div className="mt-1 flex justify-end gap-2">
          <Button variant="ghost" onClick={handleClose} disabled={start.isPending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={handleSubmit} disabled={start.isPending}>
            {start.isPending ? <Spinner className="h-4 w-4" /> : null}
            {start.isPending ? "Starting" : "Start stream"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
