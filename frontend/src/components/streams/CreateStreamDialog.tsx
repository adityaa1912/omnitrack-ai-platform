import { useState } from "react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/TextField";
import { Toggle } from "@/components/ui/Toggle";
import { Spinner } from "@/components/ui/Spinner";
import { useStartStream } from "@/hooks/useStreams";
import { ApiError } from "@/lib/api/client";
import type { StartStreamRequest } from "@/types/api";

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
  const [formError, setFormError] = useState<string | null>(null);

  const reset = () => {
    setStreamId("");
    setSource("0");
    setTracking(true);
    setConfidence("0.5");
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

    // Webcam index is numeric; file paths / RTSP URLs are strings.
    const parsedSource: number | string = /^\d+$/.test(source.trim())
      ? Number(source.trim())
      : source.trim();

    const payload: StartStreamRequest = {
      stream_id: id,
      source: parsedSource,
      tracking_enabled: tracking,
      confidence_threshold: conf,
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
      <div className="flex flex-col gap-4">
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
        <TextField
          label="Confidence threshold"
          value={confidence}
          onChange={(e) => setConfidence(e.target.value)}
          inputMode="decimal"
        />
        <Toggle label="Object tracking" checked={tracking} onChange={setTracking} />

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
