"""Mock detector for performance testing.

Returns synthetic detections with a configurable artificial latency to
simulate real model cost.  No YOLO model is loaded, no GPU is required,
and no ``ultralytics`` import happens.

The class duck-types the ``inference.detector.Detector`` interface (the
subset actually consumed by ``InferenceStream._process_frame`` and the
``BatchCoordinator``).  It is **not** a subclass — ``Detector.__init__``
would load a real model — so it replaces the detector attribute on the
stream directly via the perf harness.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from inference.types import Detection, Frame, InferenceResult


# COCO class names subset used for synthetic detections.
_CLASSES = ["person", "car", "bicycle", "dog", "cat"]


@dataclass(frozen=True)
class _FakeProvider:
    """Minimal provider stub that satisfies ``detector.provider.name``."""
    name: str = "mock"

    def describe(self) -> str:
        return "MockDetector (no real inference)"


class MockDetector:
    """Drop-in replacement for ``Detector`` that skips real inference.

    Parameters
    ----------
    latency_ms : float
        Artificial per-frame sleep that simulates model forward-pass cost.
        Set to ``0`` to measure pure pipeline overhead.
    num_detections : int
        Number of fixed bounding boxes returned per frame.
    image_size : tuple[int, int]
        ``(width, height)`` used to scale the synthetic bounding boxes so
        they fall within the frame.  Defaults to 640×480.
    """

    def __init__(
        self,
        latency_ms: float = 10.0,
        num_detections: int = 5,
        image_size: tuple[int, int] = (640, 480),
    ) -> None:
        self._latency_s = latency_ms / 1000.0
        self._num_detections = num_detections
        self.provider = _FakeProvider()
        self.config = _MinimalConfig()
        self.model = object()  # non-None sentinel (used in _cleanup check)

        # Pre-compute fixed detections (thread-safe: read-only after init).
        w, h = image_size
        self._detections = self._build_detections(w, h, num_detections)

    # -- Detector public interface ----------------------------------------

    def predict(self, frame: Frame) -> InferenceResult:
        """Simulate inference: sleep + return fixed detections."""
        if self._latency_s > 0:
            time.sleep(self._latency_s)
        return InferenceResult(
            frame=frame,
            detections=list(self._detections),  # shallow copy
            inference_time_ms=self._latency_s * 1000.0,
            model_name="mock",
        )

    def predict_batch(self, frames: list[Frame]) -> list[InferenceResult]:
        """Batch path — same artificial latency (amortised)."""
        per_frame_s = self._latency_s / max(len(frames), 1)
        if self._latency_s > 0:
            time.sleep(self._latency_s)
        return [
            InferenceResult(
                frame=f,
                detections=list(self._detections),
                inference_time_ms=per_frame_s * 1000.0,
                model_name="mock",
            )
            for f in frames
        ]

    # -- internals --------------------------------------------------------

    @staticmethod
    def _build_detections(w: int, h: int, n: int) -> list[Detection]:
        """Generate *n* evenly-spaced bounding boxes within *w*×*h*."""
        dets: list[Detection] = []
        box_w = w / (n + 1)
        box_h = h / (n + 1)
        for i in range(n):
            cx = (i + 1) * (w / (n + 1))
            cy = h / 2
            dets.append(
                Detection(
                    x1=cx - box_w / 2,
                    y1=cy - box_h / 2,
                    x2=cx + box_w / 2,
                    y2=cy + box_h / 2,
                    class_id=i % len(_CLASSES),
                    confidence=0.85,
                    class_name=_CLASSES[i % len(_CLASSES)],
                    track_id=None,
                )
            )
        return dets

    def _resize_for_inference(self, data: np.ndarray) -> np.ndarray:
        """No-op: mock detector does not resize."""
        return data


@dataclass
class _MinimalConfig:
    """Minimal stub matching the config fields ``Detector.config`` exposes."""
    model_name: str = "mock"
    confidence_threshold: float = 0.5
    iou_threshold: float = 0.45
    device: str = "cpu"
    inference_width: int = 0
    inference_height: int = 0
    enable_fp16: bool = False
    inference_provider: str = "mock"
    benchmark_enabled: bool = False
    benchmark_runs: int = 0
