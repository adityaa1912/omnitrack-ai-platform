"""Event clip generation with pre/post buffering, bounded temp assets."""

import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty


def generate_clip(pre_frames, post_duration, frame_queue, output_path, storage) -> dict:
    """Encode an event clip from pre frames plus post-duration frames.

    Returns a dict with ``file_path`` and ``ok``. ``ok`` is False when no
    frames are available (no successful evidence is manufactured for an empty
    event). Temp JPEG dirs are always cleaned up.
    """
    full_path = storage._full_path(output_path)
    parent = full_path.parent
    parent.mkdir(parents=True, exist_ok=True)

    import numpy as np
    import cv2

    frames = list(pre_frames)
    if frame_queue is not None:
        # Drain post-event frames for up to post_duration seconds.
        deadline = time.time() + max(float(post_duration or 0.0), 0.0)
        while True:
            try:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                frame = frame_queue.get(timeout=min(remaining, 0.25))
            except Empty:
                break
            if frame is None:
                break
            frames.append(frame)

    if not frames:
        return {"file_path": output_path, "ok": False, "metadata": {"reason": "no frames"}}

    tmp_dir = tempfile.mkdtemp(prefix="clip_frames_")
    jpg_paths = []
    try:
        for i, frame in enumerate(frames):
            image = None
            if isinstance(frame, dict):
                image = frame.get("image")
            elif isinstance(frame, np.ndarray):
                image = frame
            elif hasattr(frame, "tobytes"):
                try:
                    np_arr = np.frombuffer(frame.tobytes(), dtype=np.uint8)
                    if np_arr.ndim == 1 and np_arr.size > 0:
                        image = np_arr
                except Exception:  # noqa: BLE001
                    pass
            if image is None:
                continue
            if isinstance(image, np.ndarray):
                if image.ndim == 2:
                    image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            if image is None:
                continue
            jpg_path = os.path.join(tmp_dir, f"{i:06d}.jpg")
            try:
                cv2.imwrite(jpg_path, image)
                jpg_paths.append(jpg_path)
            except Exception:  # noqa: BLE001
                pass

        if jpg_paths:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-framerate", "10",
                    "-i", os.path.join(tmp_dir, "%06d.jpg"),
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    str(full_path),
                ],
                capture_output=True,
                check=True,
            )
            ok = full_path.is_file() and full_path.stat().st_size > 0
        else:
            ok = False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {
        "file_path": output_path,
        "ok": ok,
        "metadata": {"created": datetime.now(timezone.utc).isoformat(), "frame_count": len(frames)},
    }
