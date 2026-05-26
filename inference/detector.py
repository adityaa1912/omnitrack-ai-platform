import logging
import time
from typing import Optional

import numpy as np
from ultralytics import YOLO

from .config import DetectorConfig
from .types import Detection, Frame, InferenceResult


logger = logging.getLogger(__name__)


class Detector:
    """
    YOLOv8 detector wrapper with context manager support.

    Loads model once and reuses for inference. Abstracts model interface to
    support future swaps to TensorRT, quantized models, or remote services.

    Extensibility: In Phase 3, replace with TensorRTDetector or RemoteDetector
    that inherits from this interface.
    """

    def __init__(self, config: DetectorConfig) -> None:
        self.config = config
        self.model: Optional[YOLO] = None
        self._load_model()

    def _load_model(self) -> None:
        """Load YOLOv8 model from file."""
        logger.info(f"Loading model: {self.config.model_name}")
        try:
            self.model = YOLO(self.config.model_name)
            self.model.to(self.config.device)

            logger.info(
                f"Model loaded successfully on {self.config.device}. "
                f"Classes: {len(self.model.names)}"
            )
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

    def predict(self, frame: Frame) -> InferenceResult:
        """
        Run inference on a single frame.

        Args:
            frame: Frame object containing image data

        Returns:
            InferenceResult with detections, inference time, and frame metadata
        """
        if self.model is None:
            raise RuntimeError("Model not loaded. Call _load_model() first.")

        start_time = time.time()

        results = self.model.predict(
            frame.data,
            conf=self.config.confidence_threshold,
            iou=self.config.iou_threshold,
            verbose=False,
        )

        inference_time_ms = (time.time() - start_time) * 1000

        detections = self._parse_results(results[0])

        return InferenceResult(
            frame=frame,
            detections=detections,
            inference_time_ms=inference_time_ms,
            model_name=self.config.model_name,
        )

    def _parse_results(self, result) -> list[Detection]:
        """
        Parse YOLO results into Detection objects.

        Converts ultralytics result format to our Detection dataclass
        for clean decoupling from the library.
        """
        detections = []

        if result.boxes is None or len(result.boxes) == 0:
            return detections

        boxes = result.boxes.xyxy.cpu().numpy()  # (x1, y1, x2, y2)
        confs = result.boxes.conf.cpu().numpy()  # confidence scores
        class_ids = result.boxes.cls.cpu().numpy().astype(int)  # class indices

        for box, conf, class_id in zip(boxes, confs, class_ids):
            x1, y1, x2, y2 = box.astype(float)
            class_name = self.model.names[class_id]

            detection = Detection(
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                class_id=class_id,
                confidence=float(conf),
                class_name=class_name,
            )
            detections.append(detection)

        logger.debug(f"Detected {len(detections)} objects")
        return detections

    def __enter__(self) -> "Detector":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - cleanup resources."""
        self.model = None
        logger.debug("Detector resources released")
