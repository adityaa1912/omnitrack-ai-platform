from dataclasses import dataclass, field
import argparse
import logging
from pathlib import Path

from .tracking_config import TrackingConfig


@dataclass
class DetectorConfig:
    """Configuration for YOLOv8 detector."""
    model_name: str = "yolov8n.pt"
    confidence_threshold: float = 0.5
    iou_threshold: float = 0.45
    device: str = "cpu"
    # Optional inference input resolution; (0, 0) = use the source frame as-is.
    # When set, frames are resized once before the forward pass, reducing the
    # model's input size (and so its latency) at the cost of some accuracy.
    inference_width: int = 0
    inference_height: int = 0
    # Enable FP16 half-precision when the device supports it (GPU only; on CPU
    # this is a no-op). Halves memory and can speed up the forward pass.
    enable_fp16: bool = False
    # Inference backend: "torch" (ultralytics/PyTorch, default) or "onnx"
    # (ONNX Runtime CPUExecutionProvider). Selecting "onnx" exports the .pt
    # weights to a cached .onnx graph on first use and runs that instead.
    inference_provider: str = "torch"
    # Auto mode only: benchmark available providers once and pick the fastest,
    # caching results next to the model. Disabled by default so startup stays
    # lazy (auto falls back to the fixed priority order).
    benchmark_enabled: bool = False
    benchmark_runs: int = 5


@dataclass
class FrameSourceConfig:
    """Configuration for frame input source."""
    source: int | str = 0  # 0 for webcam, path/URL for file/stream
    width: int = 640
    height: int = 480
    fps: int = 30
    retry_attempts: int = 3
    retry_delay_sec: float = 5.0
    stream_timeout_sec: float = 10.0  # RTSP stream health check timeout
    backoff_initial_sec: float = 1.0  # Initial reconnection delay
    backoff_max_sec: float = 30.0  # Maximum reconnection delay
    backoff_factor: float = 2.0  # Exponential multiplier for backoff


@dataclass
class VisualizerConfig:
    """Configuration for result visualization."""
    show_fps: bool = True
    show_confidence: bool = True
    line_thickness: int = 2
    font_scale: float = 0.6
    show_trajectories: bool = True
    trajectory_thickness: int = 2
    class_colors: dict[int, tuple[int, int, int]] = field(default_factory=dict)


@dataclass
class AppConfig:
    """Application-level configuration."""
    detector_config: DetectorConfig = field(default_factory=DetectorConfig)
    frame_source_config: FrameSourceConfig = field(default_factory=FrameSourceConfig)
    visualizer_config: VisualizerConfig = field(default_factory=VisualizerConfig)
    tracking_config: TrackingConfig = field(default_factory=TrackingConfig)
    log_level: str = "INFO"
    debug: bool = False
    graceful_shutdown_timeout_sec: float = 5.0

    @staticmethod
    def from_args() -> "AppConfig":
        """Parse command-line arguments and build AppConfig."""
        parser = argparse.ArgumentParser(
            description="Real-time YOLOv8 object detection pipeline"
        )

        # Detector options
        parser.add_argument(
            "--model",
            type=str,
            default="yolov8n.pt",
            help="YOLOv8 model file (default: yolov8n.pt)",
        )
        parser.add_argument(
            "--conf",
            type=float,
            default=0.5,
            help="Confidence threshold (default: 0.5)",
        )
        parser.add_argument(
            "--iou",
            type=float,
            default=0.45,
            help="IOU threshold for NMS (default: 0.45)",
        )
        parser.add_argument(
            "--device",
            type=str,
            default="cpu",
            choices=["cpu", "cuda", "mps"],
            help="Device to run inference on (default: cpu)",
        )

        # Frame source options
        parser.add_argument(
            "--source",
            type=lambda s: int(s) if s.isdigit() else s,
            default=0,
            help="Input source: 0 for webcam, or path/URL (default: 0)",
        )
        parser.add_argument(
            "--width",
            type=int,
            default=640,
            help="Frame width (default: 640)",
        )
        parser.add_argument(
            "--height",
            type=int,
            default=480,
            help="Frame height (default: 480)",
        )
        parser.add_argument(
            "--fps",
            type=int,
            default=30,
            help="Target FPS (default: 30)",
        )

        # Stream source options (RTSP/video file)
        parser.add_argument(
            "--stream-timeout",
            type=float,
            default=10.0,
            help="RTSP stream idle timeout for reconnection (seconds, default: 10.0)",
        )
        parser.add_argument(
            "--backoff-initial",
            type=float,
            default=1.0,
            help="Initial RTSP reconnection delay (seconds, default: 1.0)",
        )
        parser.add_argument(
            "--backoff-max",
            type=float,
            default=30.0,
            help="Maximum RTSP reconnection delay (seconds, default: 30.0)",
        )
        parser.add_argument(
            "--backoff-factor",
            type=float,
            default=2.0,
            help="RTSP reconnection backoff multiplier (default: 2.0 = exponential)",
        )

        # Visualizer options
        parser.add_argument(
            "--line-thickness",
            type=int,
            default=2,
            help="Bounding box line thickness (default: 2)",
        )
        parser.add_argument(
            "--font-scale",
            type=float,
            default=0.6,
            help="Text font scale (default: 0.6)",
        )
        parser.add_argument(
            "--no-conf",
            action="store_true",
            help="Hide confidence scores in output",
        )
        parser.add_argument(
            "--no-fps",
            action="store_true",
            help="Hide FPS counter in output",
        )

        # Tracking options
        parser.add_argument(
            "--no-tracking",
            action="store_true",
            help="Disable multi-object tracking",
        )
        parser.add_argument(
            "--track-dist",
            type=float,
            default=50.0,
            help="Centroid distance threshold for matching (pixels, default: 50.0)",
        )
        parser.add_argument(
            "--max-age",
            type=int,
            default=30,
            help="Frames before deleting lost tracks (default: 30)",
        )
        parser.add_argument(
            "--no-trajectories",
            action="store_true",
            help="Hide trajectory paths in output",
        )

        # Application options
        parser.add_argument(
            "--log-level",
            type=str,
            default="INFO",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
            help="Logging level (default: INFO)",
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Enable debug mode with verbose output",
        )

        args = parser.parse_args()

        detector_config = DetectorConfig(
            model_name=args.model,
            confidence_threshold=args.conf,
            iou_threshold=args.iou,
            device=args.device,
        )

        frame_source_config = FrameSourceConfig(
            source=args.source,
            width=args.width,
            height=args.height,
            fps=args.fps,
            stream_timeout_sec=args.stream_timeout,
            backoff_initial_sec=args.backoff_initial,
            backoff_max_sec=args.backoff_max,
            backoff_factor=args.backoff_factor,
        )

        visualizer_config = VisualizerConfig(
            show_fps=not args.no_fps,
            show_confidence=not args.no_conf,
            line_thickness=args.line_thickness,
            font_scale=args.font_scale,
            show_trajectories=not args.no_trajectories,
            trajectory_thickness=args.line_thickness,
        )

        tracking_config = TrackingConfig(
            enabled=not args.no_tracking,
            centroid_distance_threshold=args.track_dist,
            max_age=args.max_age,
        )

        return AppConfig(
            detector_config=detector_config,
            frame_source_config=frame_source_config,
            visualizer_config=visualizer_config,
            tracking_config=tracking_config,
            log_level=args.log_level,
            debug=args.debug,
        )
