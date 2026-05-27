from dataclasses import dataclass


@dataclass
class TrackingConfig:
    """
    Configuration for centroid-based multi-object tracking.

    Simple, lightweight tracking using Euclidean distance between object centroids.
    Extensibility: Can be extended with ByteTrack/BoT-SORT backends using same interface.
    """
    enabled: bool = True

    # Centroid matching parameters
    centroid_distance_threshold: float = 50.0  # Pixels for centroid matching
    max_age: int = 30  # Frames before deleting lost tracks

    # Performance
    max_trajectory_points: int = 100
