/**
 * React adapter over StreamSocket.
 *
 * Owns the socket for a stream's lifetime, wires frame/status into the
 * Zustand store, and tears everything down on unmount. Subscribe only when a
 * consumer mounts, so we never hold idle sockets.
 */

import { useEffect } from "react";
import { StreamSocket } from "@/lib/ws/StreamSocket";
import { useStreamStore } from "@/store/useStreamStore";

export function useStreamSocket(streamId: string, enabled = true) {
  const setStatus = useStreamStore((s) => s.setStatus);
  const setFrame = useStreamStore((s) => s.setFrame);
  const removeStream = useStreamStore((s) => s.removeStream);

  useEffect(() => {
    if (!enabled || !streamId) return;

    const socket = new StreamSocket(streamId, {
      onFrame: (frame) => setFrame(streamId, frame),
      onStatus: (status) => setStatus(streamId, status),
    });
    socket.connect();

    return () => {
      socket.close();
      removeStream(streamId);
    };
  }, [streamId, enabled, setStatus, setFrame, removeStream]);
}
