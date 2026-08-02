"""OpenVINO inference provider.

Compiles the exported YOLOv8 ONNX graph through OpenVINO's runtime, targeting
Intel CPUs (and integrated GPUs where present). The ONNX model is produced once
via the same cached ``.onnx`` path used by the ONNX provider, then read and
compiled by OpenVINO; the compiled model is cached on disk via OpenVINO's model
cache so subsequent startups skip recompilation.

Preprocessing and postprocessing are inherited verbatim from
:class:`~backend.inference.providers.onnx_provider.ONNXProvider`: the network
head is identical, so the letterbox, decode, and per-class NMS logic is shared
rather than duplicated. Only model loading and the forward pass are specialized
here. The provider emits the same ``RawDetection`` tuples as every other
backend, so the Detector and everything downstream are unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

import numpy as np

from inference.config import DetectorConfig

from .base import RawDetection
from .onnx_provider import ONNXProvider

logger = logging.getLogger(__name__)

try:
    import openvino as ov
except Exception:  # noqa: BLE001 - absence is a valid, supported state
    ov = None  # type: ignore[assignment]


def is_available() -> bool:
    """Return whether OpenVINO is importable and exposes a usable CPU device.

    Used by automatic provider selection to gate OpenVINO onto hosts where its
    runtime is installed and a supported (Intel) device is present. Any failure
    is treated as "not available" so selection falls through to the next
    provider rather than raising.
    """
    if ov is None:
        return False
    try:
        return "CPU" in ov.Core().available_devices
    except Exception:  # noqa: BLE001 - unavailable is a valid state
        return False


class OpenVINOProvider(ONNXProvider):
    """Runs YOLOv8 through the OpenVINO runtime (Intel CPU/iGPU)."""

    name = "openvino"

    def __init__(self, config: DetectorConfig) -> None:
        super().__init__(config)
        self._core: Optional["ov.Core"] = None
        self._compiled = None
        self._ov_input = None
        self._ov_output = None
        self._device: str = "CPU"

    def _cache_dir(self) -> str:
        root, _ = os.path.splitext(self.config.model_name)
        return root + "_ov_cache"

    def _ir_path(self) -> str:
        root, _ = os.path.splitext(self.config.model_name)
        return root + "_openvino.xml"

    def load(self) -> None:
        if ov is None:
            raise RuntimeError(
                "openvino is not installed. Install it (pip install openvino) "
                "or set OMNITRACK_INFERENCE_PROVIDER to onnx or torch."
            )
        self._core = ov.Core()
        cache_dir = self._cache_dir()
        os.makedirs(cache_dir, exist_ok=True)
        self._core.set_property({"CACHE_DIR": cache_dir})
        ir_path = self._ir_path()
        if os.path.exists(ir_path):
            model = self._core.read_model(ir_path)
        else:
            onnx_path = self._ensure_model()
            model = self._core.read_model(onnx_path)
            ov.save_model(model, ir_path)
        devices = self._core.available_devices
        self._device = "AUTO" if any(d != "CPU" for d in devices) else "CPU"
        self._compiled = self._core.compile_model(model, self._device)
        self._ov_input = self._compiled.inputs[0]
        self._ov_output = self._compiled.outputs[0]
        shape = self._ov_input.get_partial_shape()
        if shape.rank.is_static and len(shape) == 4:
            dim = shape[2]
            if dim.is_static and dim.get_length() > 0:
                self._input_size = dim.get_length()
            else:
                self._input_size = self.config.inference_width or 640
        self._names = self._load_class_names()
        logger.info(
            f"[openvino] Compiled. device={self._device} "
            f"size={self._input_size} classes={len(self._names)}"
        )

    def warmup(self) -> None:
        if self._compiled is None:
            return
        dummy = np.zeros((self._input_size, self._input_size, 3), dtype=np.uint8)
        self.predict(dummy)

    def predict(self, data: np.ndarray) -> List[RawDetection]:
        if self._compiled is None:
            raise RuntimeError("OpenVINOProvider model not loaded.")
        tensor, scale, (pad_x, pad_y) = self._preprocess(data)
        result = self._compiled({self._ov_input: tensor})
        output = result[self._ov_output]
        return self._postprocess(output, scale, pad_x, pad_y)

    def describe(self) -> str:
        return f"openvino (device={self._device})"
