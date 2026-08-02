"""Inference provider abstraction.

A provider owns the model and the forward pass for one backend (PyTorch via
ultralytics, or ONNX Runtime). The :class:`~inference.detector.Detector` keeps
the public interface, timing, input resize, and result parsing, and delegates
only the model-specific work to a provider. This keeps the two backends
symmetric and avoids duplicating preprocessing/postprocessing logic.

Both providers return detections in the *same* normalized intermediate form —
a list of ``(x1, y1, x2, y2, confidence, class_id)`` tuples in the coordinate
space of the array passed to :meth:`predict` — so the Detector's parsing and
the downstream tracker/renderer/event code are provider-agnostic.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List, Tuple

import numpy as np

from inference.config import DetectorConfig

logger = logging.getLogger(__name__)

# One raw detection: (x1, y1, x2, y2, confidence, class_id).
RawDetection = Tuple[float, float, float, float, float, int]


class InferenceProvider(ABC):
    """Backend-specific model + forward pass.

    Implementations load a model, run a single forward pass on a BGR uint8
    image, and return raw detections. Class-name resolution and Detection
    dataclass construction live in the Detector so they are shared.
    """

    name: str = "abstract"

    def __init__(self, config: DetectorConfig) -> None:
        self.config = config

    @abstractmethod
    def load(self) -> None:
        """Load the model and any runtime state. Called once at startup."""

    @abstractmethod
    def warmup(self) -> None:
        """Run one throwaway forward pass to move init cost off the first frame."""

    @abstractmethod
    def predict(self, data: np.ndarray) -> List[RawDetection]:
        """Run inference on one BGR uint8 image; return raw detections."""

    @abstractmethod
    def class_names(self) -> dict[int, str]:
        """Return the class-id → name mapping used to label detections."""

    def describe(self) -> str:
        """Short human-readable description for startup logs."""
        return self.name
