import logging
from typing import Optional

import cv2
import numpy as np

from .config import VisualizerConfig
from .frame_pool import get_frame_pool
from .types import Detection, InferenceResult
from .tracking_types import TrackedObject, TrackingResult


logger = logging.getLogger(__name__)


class FpsCounter:
    """
    Exponential moving average FPS tracker.

    Uses EMA to smooth noisy instantaneous measurements.
    Extensibility: In Phase 5, replace with Prometheus metrics for export.
    """

    def __init__(self, alpha: float = 0.05) -> None:
        self.alpha = alpha  # Smoothing factor (lower = more smoothing)
        self.fps = 0.0
        self._last_inference_time = 0.0

    def update(self, inference_time_ms: float) -> None:
        """
        Update FPS estimate based on latest inference time.

        Args:
            inference_time_ms: Last inference duration in milliseconds
        """
        if inference_time_ms > 0:
            instantaneous_fps = 1000.0 / inference_time_ms
            if self.fps == 0:
                self.fps = instantaneous_fps
            else:
                self.fps = (1 - self.alpha) * self.fps + self.alpha * instantaneous_fps


class Visualizer:
    """
    Renders detection results on frames.

    Features:
    - Configurable styling (thickness, font, colors)
    - Labels with class name and confidence
    - FPS counter overlay
    - Optimized rendering (minimal allocations)

    Extensibility: In Phase 4+, replace with headless metrics export
    (no rendering for distributed inference servers).
    """

    # Standard COCO dataset colors (BGR)
    DEFAULT_COLORS = [
        (255, 0, 0),    # Blue
        (0, 255, 0),    # Green
        (0, 0, 255),    # Red
        (255, 255, 0),  # Cyan
        (255, 0, 255),  # Magenta
        (0, 255, 255),  # Yellow
    ]

    def __init__(self, config: VisualizerConfig) -> None:
        self.config = config
        self._setup_colors()
        self._font = cv2.FONT_HERSHEY_SIMPLEX

    def _setup_colors(self) -> None:
        """Initialize color palette for class visualization."""
        if self.config.class_colors:
            self.colors = self.config.class_colors
        else:
            self.colors = {}

    def render(
        self,
        frame: np.ndarray,
        result: InferenceResult,
        fps: Optional[float] = None,
    ) -> np.ndarray:
        """
        Render detections on frame.

        Args:
            frame: Input image array (H, W, 3)
            result: Inference result with detections
            fps: Current FPS for overlay (optional)

        Returns:
            Frame with bounding boxes, labels, and FPS counter
        """
        output = get_frame_pool().copy_of(frame)

        for detection in result.detections:
            self._draw_detection(output, detection)

        if self.config.show_fps and fps is not None:
            self._draw_fps(output, fps)

        if self.config.show_confidence:
            self._draw_stats(output, result)

        return output

    def _draw_detection(self, frame: np.ndarray, detection: Detection) -> None:
        """Draw single detection bounding box and label."""
        x1, y1, x2, y2 = int(detection.x1), int(detection.y1), int(detection.x2), int(detection.y2)

        color = self._get_color(detection.class_id)

        cv2.rectangle(
            frame,
            (x1, y1),
            (x2, y2),
            color,
            self.config.line_thickness,
        )

        label = detection.class_name
        if self.config.show_confidence:
            label += f" {detection.confidence:.2f}"

        font_scale = self.config.font_scale
        font_thickness = max(1, self.config.line_thickness // 2)
        text_size = cv2.getTextSize(label, self._font, font_scale, font_thickness)[0]

        text_bg_y1 = max(10, y1 - text_size[1] - 4)
        text_bg_x1 = x1
        text_bg_x2 = text_bg_x1 + text_size[0] + 4
        text_bg_y2 = text_bg_y1 + text_size[1] + 4

        cv2.rectangle(frame, (text_bg_x1, text_bg_y1), (text_bg_x2, text_bg_y2), color, -1)

        cv2.putText(
            frame,
            label,
            (text_bg_x1 + 2, text_bg_y2 - 2),
            self._font,
            font_scale,
            (255, 255, 255),
            font_thickness,
        )

    def _draw_fps(self, frame: np.ndarray, fps: float) -> None:
        """Draw FPS counter on top-left corner."""
        font_scale = self.config.font_scale
        font_thickness = max(1, self.config.line_thickness // 2)

        fps_text = f"FPS: {fps:.1f}"
        text_size = cv2.getTextSize(fps_text, self._font, font_scale, font_thickness)[0]

        x = 10
        y = 30
        padding = 4

        cv2.rectangle(
            frame,
            (x, y - text_size[1] - padding),
            (x + text_size[0] + 2 * padding, y + padding),
            (0, 0, 0),
            -1,
        )

        cv2.putText(
            frame,
            fps_text,
            (x + padding, y),
            self._font,
            font_scale,
            (0, 255, 0),
            font_thickness,
        )

    def _draw_stats(self, frame: np.ndarray, result: InferenceResult) -> None:
        """Draw inference stats on bottom-left corner."""
        font_scale = self.config.font_scale * 0.8
        font_thickness = max(1, self.config.line_thickness // 2)

        stats_text = f"Inference: {result.inference_time_ms:.1f}ms | Objects: {result.num_detections}"
        text_size = cv2.getTextSize(stats_text, self._font, font_scale, font_thickness)[0]

        h = frame.shape[0]
        x = 10
        y = h - 10
        padding = 4

        cv2.rectangle(
            frame,
            (x, y - text_size[1] - padding),
            (x + text_size[0] + 2 * padding, y + padding),
            (0, 0, 0),
            -1,
        )

        cv2.putText(
            frame,
            stats_text,
            (x + padding, y),
            self._font,
            font_scale,
            (200, 200, 200),
            font_thickness,
        )

    def _get_color(self, class_id: int) -> tuple[int, int, int]:
        """Get color for class ID."""
        if class_id not in self.colors:
            self.colors[class_id] = self.DEFAULT_COLORS[
                class_id % len(self.DEFAULT_COLORS)
            ]
        return self.colors[class_id]

    def render_tracked(
        self,
        frame: np.ndarray,
        tracking_result: TrackingResult,
        fps: Optional[float] = None,
    ) -> np.ndarray:
        """
        Render tracked objects with IDs and trajectories.

        Args:
            frame: Input image array (H, W, 3)
            tracking_result: Tracking result with tracked objects
            fps: Current FPS for overlay (optional)

        Returns:
            Frame with tracked objects, IDs, trajectories, and FPS counter
        """
        output = get_frame_pool().copy_of(frame)

        # Draw trajectories first (background)
        if self.config.show_trajectories:
            for tracked_obj in tracking_result.tracked_objects:
                self._draw_trajectory(output, tracked_obj)

        # Draw detections with track IDs
        for tracked_obj in tracking_result.tracked_objects:
            detection = tracked_obj.current_detection
            if detection is not None:
                self._draw_tracked_detection(output, detection, tracked_obj.track_id)

        if self.config.show_fps and fps is not None:
            self._draw_fps(output, fps)

        self._draw_tracking_stats(output, tracking_result)

        return output

    def _draw_tracked_detection(
        self,
        frame: np.ndarray,
        detection: Detection,
        track_id: int,
    ) -> None:
        """Draw bounding box with tracking ID."""
        x1, y1, x2, y2 = int(detection.x1), int(detection.y1), int(detection.x2), int(detection.y2)
        color = self._get_color(detection.class_id)

        cv2.rectangle(
            frame,
            (x1, y1),
            (x2, y2),
            color,
            self.config.line_thickness,
        )

        label = f"ID:{track_id} {detection.class_name}"
        if self.config.show_confidence:
            label += f" {detection.confidence:.2f}"

        font_scale = self.config.font_scale
        font_thickness = max(1, self.config.line_thickness // 2)
        text_size = cv2.getTextSize(label, self._font, font_scale, font_thickness)[0]

        text_bg_y1 = max(10, y1 - text_size[1] - 4)
        text_bg_x1 = x1
        text_bg_x2 = text_bg_x1 + text_size[0] + 4
        text_bg_y2 = text_bg_y1 + text_size[1] + 4

        cv2.rectangle(frame, (text_bg_x1, text_bg_y1), (text_bg_x2, text_bg_y2), color, -1)

        cv2.putText(
            frame,
            label,
            (text_bg_x1 + 2, text_bg_y2 - 2),
            self._font,
            font_scale,
            (255, 255, 255),
            font_thickness,
        )

    def _draw_trajectory(
        self,
        frame: np.ndarray,
        tracked_obj: TrackedObject,
    ) -> None:
        """Draw trajectory path for tracked object."""
        trajectory_array = tracked_obj.get_trajectory_array()
        if trajectory_array is None or len(trajectory_array) < 2:
            return

        color = self._get_color(tracked_obj.track_id % 80)
        thickness = getattr(self.config, 'trajectory_thickness', 2)

        trajectory_points = trajectory_array.astype(np.int32)

        for i in range(len(trajectory_points) - 1):
            pt1 = tuple(trajectory_points[i])
            pt2 = tuple(trajectory_points[i + 1])

            # Fade effect: older points more transparent
            alpha = (i + 1) / len(trajectory_points)
            faded_color = tuple(int(c * alpha) for c in color)

            cv2.line(frame, pt1, pt2, faded_color, thickness)

        # Draw current position marker
        current_pt = tuple(trajectory_points[-1])
        cv2.circle(frame, current_pt, 4, color, -1)

    def _draw_tracking_stats(
        self,
        frame: np.ndarray,
        tracking_result: TrackingResult,
    ) -> None:
        """Draw tracking statistics overlay."""
        font_scale = self.config.font_scale * 0.8
        font_thickness = max(1, self.config.line_thickness // 2)

        stats_text = (
            f"Tracks: {tracking_result.active_tracks} | "
            f"Lost: {tracking_result.lost_tracks} | "
            f"Total: {tracking_result.total_tracks}"
        )
        text_size = cv2.getTextSize(stats_text, self._font, font_scale, font_thickness)[0]

        h = frame.shape[0]
        x = 10
        y = h - 35
        padding = 4

        cv2.rectangle(
            frame,
            (x, y - text_size[1] - padding),
            (x + text_size[0] + 2 * padding, y + padding),
            (0, 0, 0),
            -1,
        )

        cv2.putText(
            frame,
            stats_text,
            (x + padding, y),
            self._font,
            font_scale,
            (0, 255, 200),
            font_thickness,
        )
