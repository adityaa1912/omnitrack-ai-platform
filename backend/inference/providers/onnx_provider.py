"""ONNX Runtime inference provider.

Runs an exported YOLOv8 ONNX graph through onnxruntime's CPUExecutionProvider.
The ONNX model is produced once from the configured ``.pt`` weights via
ultralytics' exporter and cached next to the source weights; subsequent
startups reuse the cached ``.onnx`` file and never re-export.

Preprocessing (letterbox → RGB → CHW → float32/255) and postprocessing
(score threshold → per-class NMS → xyxy) are implemented here against the raw
ONNX output tensor, since onnxruntime returns the un-decoded network head
output rather than ultralytics' parsed ``Results``. The provider emits the same
``RawDetection`` tuples as the torch provider so the Detector and everything
downstream are unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

import numpy as np

from inference.config import DetectorConfig
from inference.frame_pool import get_frame_pool

from .base import InferenceProvider, RawDetection

logger = logging.getLogger(__name__)

# onnxruntime is an optional dependency: it is only required when the ONNX
# provider is selected. Import lazily so the default torch path never pays for
# (or fails on) its absence.
try:
    import onnxruntime as ort
except Exception:  # noqa: BLE001 - absence is a valid, supported state
    ort = None  # type: ignore[assignment]


class ONNXProvider(InferenceProvider):
    """Runs YOLOv8 through ONNX Runtime (CPUExecutionProvider)."""

    name = "onnx"

    def __init__(self, config: DetectorConfig) -> None:
        super().__init__(config)
        self.session: Optional["ort.InferenceSession"] = None
        self._names: dict[int, str] = {}
        self._input_name: str = ""
        # Model's expected input square size, read from the graph at load.
        self._input_size: int = 640

    # -- model resolution / export ------------------------------------------

    def _onnx_path(self) -> str:
        """Path of the cached ONNX model, derived from the configured weights."""
        root, _ = os.path.splitext(self.config.model_name)
        return root + ".onnx"

    def _ensure_model(self) -> str:
        """Return a path to a ready ONNX model, exporting once if missing."""
        onnx_path = self._onnx_path()
        if os.path.exists(onnx_path):
            logger.info(f"[onnx] Using cached ONNX model: {onnx_path}")
            return onnx_path

        logger.info(
            f"[onnx] ONNX model not found; exporting once from "
            f"{self.config.model_name} -> {onnx_path}"
        )
        from ultralytics import YOLO

        model = YOLO(self.config.model_name)
        # imgsz matches the configured inference resolution so the exported
        # graph's static input shape lines up with what we feed it at runtime.
        imgsz = self.config.inference_width or 640
        exported = model.export(format="onnx", imgsz=imgsz, simplify=True)
        # ultralytics returns the exported path; normalize to our derived path.
        exported_path = str(exported) if exported else onnx_path
        if exported_path != onnx_path and os.path.exists(exported_path):
            os.replace(exported_path, onnx_path)
        logger.info(f"[onnx] Export complete: {onnx_path}")
        return onnx_path

    # -- lifecycle ----------------------------------------------------------

    def load(self) -> None:
        if ort is None:
            raise RuntimeError(
                "onnxruntime is not installed. Install it (pip install "
                "onnxruntime) or set OMNITRACK_INFERENCE_PROVIDER=torch."
            )
        onnx_path = self._ensure_model()
        logger.info(f"[onnx] Creating InferenceSession: {onnx_path}")
        self.session = ort.InferenceSession(
            onnx_path, providers=["CPUExecutionProvider"]
        )
        self._input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape  # e.g. [1, 3, H, W]
        if isinstance(shape, (list, tuple)) and len(shape) == 4:
            # Use the static H if present; fall back to configured/default.
            h = shape[2]
            if isinstance(h, int) and h > 0:
                self._input_size = h
            else:
                self._input_size = self.config.inference_width or 640
        self._names = self._load_class_names()
        logger.info(
            f"[onnx] Session ready. execution_provider=CPUExecutionProvider "
            f"input={self._input_name} size={self._input_size} "
            f"classes={len(self._names)}"
        )

    def _load_class_names(self) -> dict[int, str]:
        """Class names come from the source .pt weights (ONNX graph lacks them)."""
        try:
            from ultralytics import YOLO

            return dict(YOLO(self.config.model_name).names)
        except Exception as e:  # noqa: BLE001 - fall back to COCO-80 ids
            logger.warning(f"[onnx] Could not load class names ({e}); using ids")
            return {}

    def warmup(self) -> None:
        if self.session is None:
            return
        dummy = np.zeros((self._input_size, self._input_size, 3), dtype=np.uint8)
        self.predict(dummy)

    # -- inference ----------------------------------------------------------

    def _preprocess(self, data: np.ndarray) -> tuple[np.ndarray, float, tuple[int, int]]:
        """Letterbox to the model's square input, BGR→RGB, HWC→CHW, /255.

        Returns the batched tensor plus the scale and padding needed to map
        output boxes back into the input image's coordinate space.
        """
        import cv2

        pool = get_frame_pool()
        h0, w0 = data.shape[:2]
        size = self._input_size
        scale = min(size / w0, size / h0)
        nw, nh = int(round(w0 * scale)), int(round(h0 * scale))
        pad_x, pad_y = (size - nw) // 2, (size - nh) // 2
        canvas = pool.acquire((size, size, 3), np.uint8)
        canvas.fill(114)
        resized = pool.acquire((nh, nw, 3), data.dtype)
        cv2.resize(data, (nw, nh), dst=resized, interpolation=cv2.INTER_LINEAR)
        canvas[pad_y : pad_y + nh, pad_x : pad_x + nw] = resized
        pool.release(resized)
        tensor = pool.acquire((1, 3, size, size), np.float32)
        chw = canvas[:, :, ::-1].transpose(2, 0, 1)  # BGR→RGB, HWC→CHW
        np.copyto(tensor[0], chw)
        tensor[0] /= 255.0
        pool.release(canvas)
        return tensor, scale, (pad_x, pad_y)

    def predict(self, data: np.ndarray) -> List[RawDetection]:
        if self.session is None:
            raise RuntimeError("ONNXProvider session not loaded.")
        tensor, scale, (pad_x, pad_y) = self._preprocess(data)
        outputs = self.session.run(None, {self._input_name: tensor})
        get_frame_pool().release(tensor)
        return self._postprocess(outputs[0], scale, pad_x, pad_y)

    def _postprocess(
        self, output: np.ndarray, scale: float, pad_x: int, pad_y: int
    ) -> List[RawDetection]:
        """Decode the raw head output and apply per-class NMS.

        YOLOv8 ONNX output is (1, 4+num_classes, num_anchors); we transpose to
        (num_anchors, 4+num_classes), threshold on the best class score, run
        per-class NMS, and map boxes back to the input image's coordinates.
        """
        preds = np.squeeze(output, axis=0)
        if preds.ndim != 2:
            return []
        # (4+nc, anchors) -> (anchors, 4+nc)
        if preds.shape[0] < preds.shape[1]:
            preds = preds.T

        boxes_cxcywh = preds[:, :4]
        class_scores = preds[:, 4:]
        if class_scores.size == 0:
            return []
        class_ids = np.argmax(class_scores, axis=1)
        confs = class_scores[np.arange(class_scores.shape[0]), class_ids]

        keep = confs >= self.config.confidence_threshold
        if not np.any(keep):
            return []
        boxes_cxcywh = boxes_cxcywh[keep]
        confs = confs[keep]
        class_ids = class_ids[keep]

        # cx,cy,w,h -> x1,y1,x2,y2 in the letterboxed (square) space.
        cx, cy, w, h = np.split(boxes_cxcywh, 4, axis=1)
        x1 = (cx - w / 2).ravel()
        y1 = (cy - h / 2).ravel()
        x2 = (cx + w / 2).ravel()
        y2 = (cy + h / 2).ravel()

        # Undo letterbox padding + scale -> input image coordinates.
        x1 = (x1 - pad_x) / scale
        y1 = (y1 - pad_y) / scale
        x2 = (x2 - pad_x) / scale
        y2 = (y2 - pad_y) / scale

        keep_idx = self._nms(
            np.stack([x1, y1, x2, y2], axis=1), confs, class_ids
        )
        return [
            (float(x1[i]), float(y1[i]), float(x2[i]), float(y2[i]),
             float(confs[i]), int(class_ids[i]))
            for i in keep_idx
        ]

    def _nms(
        self, boxes: np.ndarray, confs: np.ndarray, class_ids: np.ndarray
    ) -> List[int]:
        """Per-class NMS via OpenCV, honoring the configured IoU threshold."""
        import cv2

        keep: List[int] = []
        for cls in np.unique(class_ids):
            idxs = np.where(class_ids == cls)[0]
            if idxs.size == 0:
                continue
            cls_boxes = boxes[idxs]
            cls_confs = confs[idxs]
            # cv2.dnn.NMSBoxes wants [x, y, w, h].
            xywh = cls_boxes.copy()
            xywh[:, 2] = cls_boxes[:, 2] - cls_boxes[:, 0]
            xywh[:, 3] = cls_boxes[:, 3] - cls_boxes[:, 1]
            picked = cv2.dnn.NMSBoxes(
                xywh.tolist(),
                cls_confs.tolist(),
                score_threshold=self.config.confidence_threshold,
                nms_threshold=self.config.iou_threshold,
            )
            if len(picked) > 0:
                keep.extend(idxs[np.atleast_1d(picked)].tolist())
        return keep

    def class_names(self) -> dict[int, str]:
        return self._names

    def describe(self) -> str:
        return "onnx (execution_provider=CPUExecutionProvider)"
