"""PyTorch/ultralytics inference provider (the default, pre-existing backend)."""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
from ultralytics import YOLO

from inference.config import DetectorConfig

from .base import InferenceProvider, RawDetection

logger = logging.getLogger(__name__)


class TorchProvider(InferenceProvider):
    """Runs YOLOv8 through ultralytics' PyTorch runtime.

    This is the original execution path, preserved verbatim. FP16 is honored
    only on non-CPU devices (it is unsupported on CPU and would error there).
    """

    name = "torch"

    def __init__(self, config: DetectorConfig) -> None:
        super().__init__(config)
        self.model: Optional[YOLO] = None

    def load(self) -> None:
        logger.info(f"[torch] Loading model: {self.config.model_name}")
        self.model = YOLO(self.config.model_name)
        self.model.to(self.config.device)
        logger.info(
            f"[torch] Model loaded on {self.config.device}. "
            f"Classes: {len(self.model.names)}"
        )

    def _use_fp16(self) -> bool:
        if not self.config.enable_fp16:
            return False
        return str(self.config.device).lower() not in ("", "cpu")

    def warmup(self) -> None:
        if self.model is None:
            return
        w = self.config.inference_width or 640
        h = self.config.inference_height or 640
        dummy = np.zeros((h, w, 3), dtype=np.uint8)
        self.model.predict(
            dummy,
            conf=self.config.confidence_threshold,
            iou=self.config.iou_threshold,
            half=self._use_fp16(),
            verbose=False,
        )

    def predict(self, data: np.ndarray) -> List[RawDetection]:
        if self.model is None:
            raise RuntimeError("TorchProvider model not loaded.")
        results = self.model.predict(
            data,
            conf=self.config.confidence_threshold,
            iou=self.config.iou_threshold,
            half=self._use_fp16(),
            verbose=False,
        )
        return self._to_raw(results[0])

    def _to_raw(self, result) -> List[RawDetection]:
        if result.boxes is None or len(result.boxes) == 0:
            return []
        boxes = result.boxes.xyxy.cpu().numpy()
        confs = result.boxes.conf.cpu().numpy()
        class_ids = result.boxes.cls.cpu().numpy().astype(int)
        return [
            (float(b[0]), float(b[1]), float(b[2]), float(b[3]), float(c), int(k))
            for b, c, k in zip(boxes, confs, class_ids)
        ]

    def class_names(self) -> dict[int, str]:
        if self.model is None:
            return {}
        return dict(self.model.names)

    def describe(self) -> str:
        return f"torch (device={self.config.device}, fp16={self._use_fp16()})"
