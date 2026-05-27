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


@dataclass
class FrameSourceConfig:
    """Configuration for frame input source."""
    source: int | str = 0  # 0 for webcam, path/URL for file/stream
    width: int = 640
    height: int = 480
    fps: int = 30
    retry_attempts: int = 3
    retry_delay_sec: float = 5.0


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
