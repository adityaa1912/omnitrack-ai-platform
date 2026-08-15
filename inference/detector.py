import logging
import time
from typing import Optional

import numpy as np

from .config import DetectorConfig
from .frame_pool import get_frame_pool
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

    if getattr(config, "benchmark_enabled", False):
        from backend.inference import benchmark

        try:
            return benchmark.select_provider(config, candidates)
        except Exception as e:  # noqa: BLE001 - fall back to fixed priority
            logger.warning(
                f"Provider benchmarking failed ({e}); using fixed priority order"
            )

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
        data = self._resize_for_inference(frame.data)

        raw = self._predict_with_fallback(data)

        if data is not frame.data:
            get_frame_pool().release(data)

        inference_time_ms = (time.time() - start_time) * 1000

        detections = self._parse_results(raw)

        return InferenceResult(
            frame=frame,
            detections=detections,
            inference_time_ms=inference_time_ms,
            model_name=self.config.model_name,
        )

    def _resize_for_inference(self, data):
        """Resize a BGR frame to the configured inference resolution, if any.

        Shared by the single-frame and batched paths so the preprocessing that
        precedes the forward pass lives in exactly one place. Returns the input
        unchanged (no copy) when no inference resolution is set or it already
        matches.
        """
        iw = self.config.inference_width
        ih = self.config.inference_height
        if iw > 0 and ih > 0 and (data.shape[1] != iw or data.shape[0] != ih):
            import cv2

            dst = get_frame_pool().acquire((ih, iw) + data.shape[2:], data.dtype)
            cv2.resize(data, (iw, ih), dst=dst, interpolation=cv2.INTER_LINEAR)
            return dst
        return data

    def predict_batch(self, frames: list[Frame]) -> list[InferenceResult]:
        """Run inference on several frames in a single fused forward pass.

        Preprocessing and result parsing are identical to :meth:`predict`; only
        the forward pass is batched, via the provider's ``predict_batch`` (a true
        batch on torch, a sequential fallback elsewhere). The measured batch time
        is amortized evenly across the frames so per-frame ``inference_time_ms``
        stays comparable to the single-frame path.
        """
        if not frames:
            return []
        start_time = time.time()
        batch = [self._resize_for_inference(f.data) for f in frames]
        raws = self.provider.predict_batch(batch)
        pool = get_frame_pool()
        for original, resized in zip(frames, batch):
            if resized is not original.data:
                pool.release(resized)
        per_frame_ms = ((time.time() - start_time) * 1000) / len(frames)
        results = []
        for frame, raw in zip(frames, raws):
            results.append(
                InferenceResult(
                    frame=frame,
                    detections=self._parse_results(raw),
                    inference_time_ms=per_frame_ms,
                    model_name=self.config.model_name,
                )
            )
        return results

    def _predict_with_fallback(self, data):
        """Run the forward pass, recovering once if the provider fails at runtime.

        A provider that starts raising mid-stream (e.g. a device/driver fault on
        an accelerator) is rebuilt to the next backend in the fixed priority
        order and the frame is retried, so a runtime provider failure degrades
        instead of killing the stream. If no fallback is available the original
        error propagates.
        """
        try:
            return self.provider.predict(data)
        except Exception as exc:  # noqa: BLE001 - attempt one live fallback
            if getattr(self, "_fallback_exhausted", False):
                raise
            logger.warning(
                f"Provider {getattr(self.provider, 'name', '?')} failed at "
                f"runtime ({exc}); falling back"
            )
            if not self._rebuild_fallback_provider():
                self._fallback_exhausted = True
                raise
            return self.provider.predict(data)

    def _rebuild_fallback_provider(self) -> bool:
        """Rebuild ``self.provider`` as ONNX then torch, skipping the failed one.

        Returns True when a different provider loaded successfully.
        """
        from backend.inference.providers import ONNXProvider, TorchProvider

        current = getattr(self.provider, "name", "")
        for builder in (ONNXProvider, TorchProvider):
            if builder.name == current:
                continue
            try:
                provider = builder(self.config)
                provider.load()
                self.provider = provider
                return True
            except Exception as e:  # noqa: BLE001 - try the next fallback
                logger.warning(
                    f"Runtime fallback to {builder.__name__} failed ({e})"
                )
        return False

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
