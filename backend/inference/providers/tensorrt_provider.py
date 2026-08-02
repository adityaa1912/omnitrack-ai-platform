"""TensorRT inference provider.

Runs the exported YOLOv8 ONNX graph through onnxruntime's TensorRT execution
provider, which builds a TensorRT engine from the ONNX model on first use and
caches it on disk. Available only on NVIDIA CUDA systems with the GPU build of
onnxruntime installed; every other host falls through the automatic provider
selection to OpenVINO/ONNX/PyTorch.

Preprocessing and postprocessing are inherited verbatim from
:class:`~backend.inference.providers.onnx_provider.ONNXProvider` — the network
head is identical, so letterbox, decode, and per-class NMS are shared rather
than duplicated. Only session construction and the runtime device differ.
FP16 is enabled when configured and the device is non-CPU; if the FP16 engine
build fails (e.g. hardware without FP16 support) the session is retried in
FP32, so inference always starts.
"""

from __future__ import annotations

import logging
import os

from inference.config import DetectorConfig

from .onnx_provider import ONNXProvider

logger = logging.getLogger(__name__)

try:
    import onnxruntime as ort
except Exception:  # noqa: BLE001 - absence is a valid, supported state
    ort = None  # type: ignore[assignment]


def is_available() -> bool:
    """Return whether TensorRT can be used on this host.

    True only when onnxruntime is installed and exposes its TensorRT execution
    provider, which the GPU build does only on NVIDIA CUDA systems. Any failure
    is treated as "not available" so automatic selection falls through to the
    next provider rather than raising.
    """
    if ort is None:
        return False
    try:
        return "TensorrtExecutionProvider" in ort.get_available_providers()
    except Exception:  # noqa: BLE001 - unavailable is a valid state
        return False


class TensorRTProvider(ONNXProvider):
    """Runs YOLOv8 through ONNX Runtime's TensorRT execution provider."""

    name = "tensorrt"

    def __init__(self, config: DetectorConfig) -> None:
        super().__init__(config)
        self._fp16: bool = False

    def _engine_cache_dir(self) -> str:
        root, _ = os.path.splitext(self.config.model_name)
        return root + "_trt_cache"

    def _use_fp16(self) -> bool:
        if not self.config.enable_fp16:
            return False
        return str(self.config.device).lower() not in ("", "cpu")

    def _session_options(self) -> "ort.SessionOptions":
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        cache_dir = self._engine_cache_dir()
        os.makedirs(cache_dir, exist_ok=True)
        opts.add_session_config_entry("trt_engine_cache_enable", "1")
        opts.add_session_config_entry("trt_engine_cache_path", cache_dir)
        return opts

    def load(self) -> None:
        if ort is None:
            raise RuntimeError(
                "onnxruntime with TensorRT support is not installed. Install "
                "onnxruntime-gpu or set OMNITRACK_INFERENCE_PROVIDER to "
                "openvino, onnx, or torch."
            )
        onnx_path = self._ensure_model()
        self._fp16 = self._use_fp16()
        try:
            self._create_session(onnx_path)
        except Exception as fp16_error:  # noqa: BLE001 - retried in FP32
            if not self._fp16:
                raise
            logger.warning(
                f"[tensorrt] FP16 engine build failed ({fp16_error}); "
                "retrying FP32"
            )
            self._fp16 = False
            self._create_session(onnx_path)

    def _create_session(self, onnx_path: str) -> None:
        logger.info(
            f"[tensorrt] Creating TensorRT session: {onnx_path} fp16={self._fp16}"
        )
        trt_opts: dict = {}
        if self._fp16:
            trt_opts["trt_fp16_enable"] = "1"
        self.session = ort.InferenceSession(
            onnx_path,
            sess_options=self._session_options(),
            providers=["TensorrtExecutionProvider", "CUDAExecutionProvider"],
            provider_options=[trt_opts, {}],
        )
        self._input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        if isinstance(shape, (list, tuple)) and len(shape) == 4:
            h = shape[2]
            if isinstance(h, int) and h > 0:
                self._input_size = h
            else:
                self._input_size = self.config.inference_width or 640
        self._names = self._load_class_names()
        logger.info(
            f"[tensorrt] Session ready. execution_provider=TensorrtExecutionProvider "
            f"fp16={self._fp16} input={self._input_name} size={self._input_size} "
            f"classes={len(self._names)}"
        )

    def describe(self) -> str:
        return (
            "tensorrt (execution_provider=TensorrtExecutionProvider, "
            f"fp16={self._fp16})"
        )
