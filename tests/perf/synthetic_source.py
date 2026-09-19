"""Synthetic frame source for performance testing.

Generates solid-colour or gradient numpy frames at a configurable resolution
and rate **without** touching OpenCV VideoCapture, so the harness runs on any
machine (CI runner, laptop, container) with zero hardware dependencies.

Conforms to the ``inference.frame_source.FrameSource`` ABC so it can be
injected into ``InferenceStream._initialize_components`` as a drop-in
replacement for the real camera / file / RTSP sources.
"""

from __future__ import annotations

import time
from typing import Generator, Optional

import numpy as np

from inference.frame_source import FrameSource
from inference.types import Frame


class SyntheticFrameSource(FrameSource):
    """Deterministic, zero-I/O frame producer for throughput measurement.

    Parameters
    ----------
    width, height : int
        Frame resolution in pixels.  Defaults match the ``StreamConfig``
        default (640 × 480).
    fps_cap : float
        Maximum frames per second.  The generator sleeps between yields to
        simulate real capture cadence.  ``0`` disables the cap (hot loop).
    total_frames : int or None
        Stop after this many frames.  ``None`` = run until ``close()`` is
        called (infinite source).
    pattern : str
        Visual pattern for generated frames:
        - ``"solid"`` – single colour (fastest; no per-pixel arithmetic).
        - ``"gradient"`` – horizontal gradient (useful for visual sanity).
    colour : tuple[int, int, int]
        BGR colour for the ``"solid"`` pattern.  Ignored by ``"gradient"``.
    """

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        fps_cap: float = 30.0,
        total_frames: Optional[int] = None,
        pattern: str = "solid",
        colour: tuple[int, int, int] = (128, 128, 128),
    ) -> None:
        self._width = width
        self._height = height
        self._fps_cap = fps_cap
        self._total_frames = total_frames
        self._pattern = pattern
        self._colour = colour

        self._is_open = False
        self._frame_id = 0
        self._template: Optional[np.ndarray] = None

    # -- FrameSource ABC --------------------------------------------------

    def open(self) -> None:
        """Pre-allocate the frame template."""
        if self._pattern == "gradient":
            row = np.linspace(0, 255, self._width, dtype=np.uint8)
            plane = np.tile(row, (self._height, 1))
            self._template = np.stack([plane, plane, plane], axis=-1)
        else:
            self._template = np.full(
                (self._height, self._width, 3),
                self._colour,
                dtype=np.uint8,
            )
        self._is_open = True
        self._frame_id = 0

    def read(self) -> Generator[Frame, None, None]:
        """Yield synthetic frames, optionally capped at ``fps_cap``."""
        if not self._is_open or self._template is None:
            raise RuntimeError(
                "SyntheticFrameSource not opened. Call open() first."
            )

        min_interval = (1.0 / self._fps_cap) if self._fps_cap > 0 else 0.0

        while self._is_open:
            if (
                self._total_frames is not None
                and self._frame_id >= self._total_frames
            ):
                return  # natural EOF — mirrors VideoFileFrameSource

            start = time.perf_counter()

            # Copy so downstream consumers (pool, imencode) own their buffer.
            data = self._template.copy()
            frame = Frame(
                timestamp=time.time(),
                frame_id=self._frame_id,
                data=data,
            )
            self._frame_id += 1
            yield frame

            # Pace to configured FPS.
            elapsed = time.perf_counter() - start
            if min_interval > 0 and elapsed < min_interval:
                time.sleep(min_interval - elapsed)

    def is_open(self) -> bool:
        return self._is_open

    def close(self) -> None:
        self._is_open = False
        self._template = None

    # -- helpers -----------------------------------------------------------

    @property
    def frames_produced(self) -> int:
        """Total frames yielded so far (useful in assertions)."""
        return self._frame_id
