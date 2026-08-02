import logging
import time
from typing import Optional

import numpy as np

from .config import DetectorConfig
from .types import Detection, Frame, InferenceResult


logger = logging.getLogger(__name__)


def _build_provider(config: DetectorConfig):
    """Construct the inference provider selected by ``config.inference_provider``.

    Imported lazily so the default torch path never imports onnxruntime or
    openvino (optional dependencies) unless that backend is actually requested.
    ``auto`` picks the fastest available backend (tensorrt on NVIDIA CUDA
    systems, then openvino on supported Intel CPUs, then onnx, then torch) and
    falls back gracefully when a selected or auto-picked backend cannot load.
    """
    from backend.inference.providers import (
        ONNXProvider,
        OpenVINOProvider,
        TensorRTProvider,
        TorchProvider,
    )

    builders = {
        "torch": TorchProvider,
        "onnx": ONNXProvider,
        "openvino": OpenVINOProvider,
        "tensorrt": TensorRTProvider,
    }

    provider = (config.inference_provider or "torch").strip().lower()
    if provider == "auto":
        return _build_auto_provider(config)
    builder = builders.get(provider)
    if builder is None:
        raise ValueError(
            f"Unknown inference_provider {config.inference_provider!r}; "
            "expected 'auto', 'torch', 'onnx', 'openvino', or 'tensorrt'."
        )
    return builder(config)


def _build_auto_provider(config: DetectorConfig):
    """Select and construct the best available provider, falling back on failure.

    Preference order is tensorrt (only on NVIDIA CUDA hosts) → openvino (only
    where its runtime and a supported Intel device are present) → onnx → torch.
    Each candidate is constructed and loaded in turn; a candidate that raises
    is logged and the next is tried, so a CPU-only host with no accelerator
    always lands on torch and behaves exactly as before.
    """
    from backend.inference.providers import (
        ONNXProvider,
        OpenVINOProvider,
        TensorRTProvider,
        TorchProvider,
    )
    from backend.inference.providers import openvino_provider, tensorrt_provider

    candidates = []
    if tensorrt_provider.is_available():
        candidates.append(TensorRTProvider)
    if openvino_provider.is_available():
        candidates.append(OpenVINOProvider)
    candidates.append(ONNXProvider)
    candidates.append(TorchProvider)

    last_error: Optional[Exception] = None
    for builder in candidates:
        provider = builder(config)
        try:
            provider.load()
            provider._auto_loaded = True
            return provider
        except Exception as e:  # noqa: BLE001 - try the next backend
            last_error = e
            logger.warning(
                f"Auto provider {builder.__name__} unavailable ({e}); "
                "trying next backend"
            )
    raise RuntimeError(
        f"No inference provider could be loaded (last error: {last_error})"
    )


class Detector:
    """
    YOLOv8 detector wrapper with context manager support.

    The Detector owns the public interface, inference timing, input resize, and
    conversion of raw provider output into :class:`Detection` objects. The
    model-specific work (load / forward pass / decode) is delegated to an
    inference provider (PyTorch or ONNX Runtime), selected by configuration.
    """

    def __init__(self, config: DetectorConfig) -> None:
        self.config = config
        self.provider = _build_provider(config)
        self._load_model()
        self._warmup()

    def _load_model(self) -> None:
        """Load the model via the selected provider.

        Auto selection loads the chosen provider while probing candidates, so a
        provider it already loaded (flagged ``_auto_loaded``) is not loaded a
        second time.
        """
        logger.info(
            f"Loading model via provider={self.provider.name}: "
            f"{self.config.model_name}"
        )
        try:
            if not getattr(self.provider, "_auto_loaded", False):
                self.provider.load()
            logger.info(
                f"Model loaded. provider={self.provider.name} "
                f"detail={self.provider.describe()}"
            )
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

    def _warmup(self) -> None:
        """Run one throwaway forward pass to warm the model.

        The first predict() after load pays one-time costs (kernel/graph init,
        allocator warmup). Burning a single dummy inference here moves that
        cost off the first real frame so initial latency is stable. Failures
        are logged and ignored — warmup must never block startup.
        """
        try:
            self.provider.warmup()
            logger.debug("Model warmup complete")
        except Exception as e:  # noqa: BLE001 - warmup is best-effort
            logger.warning(f"Model warmup failed (continuing): {e}")

    def predict(self, frame: Frame) -> InferenceResult:
        """
        Run inference on a single frame.

        Args:
            frame: Frame object containing image data

        Returns:
            InferenceResult with detections, inference time, and frame metadata
        """
        start_time = time.time()

        # Resize once, only when an inference resolution is configured and it
        # differs from the source. Otherwise the frame is used as-is (no copy).
        data = frame.data
        iw = self.config.inference_width
        ih = self.config.inference_height
        if iw > 0 and ih > 0 and (data.shape[1] != iw or data.shape[0] != ih):
            import cv2

            data = cv2.resize(data, (iw, ih), interpolation=cv2.INTER_LINEAR)

        raw = self.provider.predict(data)

        inference_time_ms = (time.time() - start_time) * 1000

        detections = self._parse_results(raw)

        return InferenceResult(
            frame=frame,
            detections=detections,
            inference_time_ms=inference_time_ms,
            model_name=self.config.model_name,
        )

    def _parse_results(self, raw) -> list[Detection]:
        """
        Convert provider raw detections into Detection objects.

        Both providers emit ``(x1, y1, x2, y2, confidence, class_id)`` tuples in
        the coordinate space of the array passed to the provider, so this
        parsing is shared and provider-agnostic.
        """
        names = self.provider.class_names()
        detections = []
        for x1, y1, x2, y2, conf, class_id in raw:
            detections.append(
                Detection(
                    x1=float(x1),
                    y1=float(y1),
                    x2=float(x2),
                    y2=float(y2),
                    class_id=int(class_id),
                    confidence=float(conf),
                    class_name=names.get(int(class_id), str(class_id)),
                )
            )

        logger.debug(f"Detected {len(detections)} objects")
        return detections

    def __enter__(self) -> "Detector":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit - cleanup resources."""
        self.provider = None
        logger.debug("Detector resources released")
