import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def generate_clip(pre_frames, post_duration, frame_queue, output_path, storage) -> dict:
    # Resolve full path within storage base directory
    full_path = storage._full_path(output_path)
    parent = full_path.parent
    parent.mkdir(parents=True, exist_ok=True)

    import numpy as np
    import cv2

    frames = list(pre_frames)
    if not frames:
        return {"file_path": output_path, "metadata": {"created": datetime.now(timezone.utc).isoformat()}}

    jpg_paths = []
    tmp_dir = tempfile.mkdtemp()
    for i, frame in enumerate(frames):
        if isinstance(frame, np.ndarray):
            if frame.ndim == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            cv2.imwrite(os.path.join(tmp_dir, f"{i:06d}.jpg"), frame)
            jpg_paths.append(os.path.join(tmp_dir, f"{i:06d}.jpg"))
        elif hasattr(frame, "convert"):
            try:
                frame.convert("RGB").save(os.path.join(tmp_dir, f"{i:06d}.jpg"))
                jpg_paths.append(os.path.join(tmp_dir, f"{i:06d}.jpg"))
            except Exception:  # noqa: BLE001
                pass
        elif hasattr(frame, "tobytes"):
            try:
                np_arr = np.frombuffer(frame.tobytes(), dtype=np.uint8)
                if len(np_arr.shape) == 3:
                    cv2.imwrite(os.path.join(tmp_dir, f"{i:06d}.jpg"), np_arr)
                    jpg_paths.append(os.path.join(tmp_dir, f"{i:06d}.jpg"))
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
    else:
        full_path.write_bytes(b"")
    return {"file_path": output_path, "metadata": {"created": datetime.now(timezone.utc).isoformat()}}
