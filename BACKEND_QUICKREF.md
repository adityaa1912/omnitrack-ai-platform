"""
FastAPI Backend - Quick Reference and Usage Guide

ARCHITECTURE OVERVIEW
=====================

The FastAPI backend provides REST + WebSocket APIs for controlling and monitoring
YOLOv8 inference streams. It reuses existing inference modules without modification.

Component Structure:
  - app.py:     FastAPI application (REST endpoints + WebSocket)
  - service.py: Inference stream manager (orchestrates detector/tracker/visualizer)
  - models.py:  Database schema (SQLAlchemy ORM)
  - run.py:     Server entry point


QUICK START
===========

1. Install dependencies:
   pip install -r requirements.txt

2. Run the backend:
   python -m backend.run

3. Access API documentation:
   http://localhost:8000/docs

4. Test with curl:
   curl http://localhost:8000/health


API ENDPOINTS - REST
====================

GET /health
  - Returns: Service health status + active stream count
  - Example: curl http://localhost:8000/health

POST /stream/start
  - Starts a new inference stream
  - Body: {
      "stream_id": "cam1",
      "source": 0,                    // 0=webcam, file path, or RTSP URL
      "width": 640,
      "height": 480,
      "fps": 30,
      "confidence_threshold": 0.5,
      "tracking_enabled": true,
      "track_distance": 50.0,
      "max_age": 30
    }
  - Example:
    curl -X POST http://localhost:8000/stream/start \
      -H "Content-Type: application/json" \
      -d '{"stream_id": "cam1", "source": 0, "tracking_enabled": true}'

POST /stream/stop
  - Stops an inference stream
  - Query param: stream_id=cam1
  - Example: curl -X POST http://localhost:8000/stream/stop?stream_id=cam1

GET /streams
  - Returns list of all active streams with metrics
  - Example: curl http://localhost:8000/streams

GET /stream/{stream_id}/metrics
  - Returns metrics for a specific stream (FPS, inference time, detection count)
  - Example: curl http://localhost:8000/stream/cam1/metrics

GET /stream/{stream_id}/detections
  - Returns latest detections from a stream
  - Example: curl http://localhost:8000/stream/cam1/detections


API ENDPOINTS - WEBSOCKET
=========================

WS /stream/{stream_id}/ws
  - Real-time frame streaming with detection overlays
  - Sends JPEG frames encoded as base64 + detection coordinates
  - Message format:
    {
      "type": "frame",
      "stream_id": "cam1",
      "timestamp": 1706800000.123,
      "frame": "base64-encoded-jpeg",
      "detections": [
        {
          "x1": 100, "y1": 100, "x2": 200, "y2": 200,
          "class_id": 0, "class_name": "person",
          "confidence": "0.95", "track_id": 1
        }
      ]
    }


ARCHITECTURE - SERVICE LAYER
=============================

InferenceService (service.py)
  - Manages multiple concurrent streams (extensible)
  - Currently: single-stream design
  - Methods:
    - start_stream(config): Start new stream
    - stop_stream(stream_id): Stop stream
    - get_stream_metrics(stream_id): Get metrics
    - get_stream_detections(stream_id): Get detections
    - get_stream_frame(stream_id): Get output frame

InferenceStream (service.py)
  - Manages single inference pipeline
  - Components:
    - FrameSource: Reads frames (webcam/file/RTSP)
    - Detector: YOLOv8 inference
    - CentroidTracker: Multi-object tracking
    - Visualizer: Renders detections + trajectories
  - Background thread for inference loop
  - Output queue for frame/detection delivery


DATABASE SCHEMA
===============

Tables:
  - detections:       Persisted detection records
  - metrics:          Inference metrics (FPS, timing)
  - stream_sessions:  Stream lifecycle and statistics

Default: SQLite (inference_data.db)
  - No external database required
  - All data persisted locally
  - Suitable for single-machine deployments


DEPENDENCY BOUNDARIES
====================

API Layer (app.py)
  - Pydantic schemas for validation
  - FastAPI endpoints
  - No direct inference logic

Service Layer (service.py)
  - Orchestrates inference pipelines
  - Manages stream lifecycle
  - Decouples API from inference modules

Inference Layer (inference/*.py)
  - Detector, FrameSource, Tracker, Visualizer
  - Unchanged from core pipeline
  - Reused without modification


STREAMING ARCHITECTURE
======================

Current: Single-stream + Future-proof design
  - One active inference pipeline
  - Designed to scale to multi-stream

Frame Delivery:
  - REST: Latest frame snapshot (detections)
  - WebSocket: Real-time frame stream (low latency)

Buffering Strategy:
  - Output queue (maxsize=30 frames)
  - Non-blocking: drops frames if queue full
  - Suitable for real-time dashboards


CONFIGURATION EXAMPLES
======================

Webcam with tracking:
  POST /stream/start
  {
    "stream_id": "webcam_office",
    "source": 0,
    "tracking_enabled": true,
    "track_distance": 50,
    "max_age": 30
  }

Video file playback:
  POST /stream/start
  {
    "stream_id": "video_review",
    "source": "/path/to/video.mp4",
    "fps": 30,
    "tracking_enabled": true
  }

RTSP camera stream:
  POST /stream/start
  {
    "stream_id": "rtsp_cam1",
    "source": "rtsp://192.168.1.100:554/stream",
    "tracking_enabled": true,
    "track_distance": 60,
    "max_age": 45
  }


FUTURE EXTENSIONS
=================

Multi-stream support:
  - Modify service.py to spawn multiple InferenceStream instances
  - Each stream gets independent pipeline
  - Load balanced metrics endpoint

GPU acceleration:
  - Add --device cuda to stream config
  - Detector already supports device selection
  - Trivial to expose in API

Distributed deployment:
  - Replace SQLite with PostgreSQL
  - Add message queue (Kafka, Redis)
  - Separate API tier from inference tier

Logging/Metrics export:
  - Add Prometheus metrics endpoint
  - Export to CloudWatch/DataDog
  - Real-time performance dashboards
"""
