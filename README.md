# OmniTrack AI Platform

👁️ Real-time object detection and tracking built as a production-ready system, not just a standalone script.

OmniTrack is a practical, modular AI surveillance pipeline designed to process real-time video streams. Instead of jamming everything into a single file, this project is built from the ground up using a clean architecture that separates frame ingestion, model inference, and rendering. This makes it easy to transition from a local webcam setup to a distributed, production system.

[Features](#-features) •
[Architecture](#-system-architecture) •
[Getting Started](#-getting-started) •
[Benchmarks](#-benchmarks) •
[Roadmap](#-roadmap)

---

## 🔥 Features

### What's working right now:
* **Clean, Modular Code:** Separate files/layers for configuration, data types, frame ingestion, detection, and rendering.
* **YOLOv8 Live Pipeline:** Streamlined integration with Ultralytics YOLOv8 for object detection and confidence scoring.
* **Safe Resource Cleanup:** Handles application shutdowns (`Ctrl+C` or `q`) cleanly without leaving hanging webcam processes or thread leaks.
* **Structured Logging:** No messy `print()` statements. Uses centralized logging to track performance metrics and FPS in real time.

### What's coming next:
* State persistence using **ByteTrack** / **BoT-SORT** to track objects across frames with unique IDs.
* **Apache Kafka** pipeline to handle multiple video streams concurrently without choking the main ML model.
* High-performance deployment using **FastAPI** and **TensorRT** acceleration.

---

## 🏗️ System Architecture

The data flows through the application linearly, ensuring that adding tracking logic or database writes won't block the video frame capture thread.

```text
  [ Webcam / RTSP Stream ]
              │
              ▼
    ┌───────────────────┐
    │   Frame Ingestion │  <-- OpenCV thread capturing raw frames
    └───────────────────┘
              │
              ▼
    ┌───────────────────┐
    │   YOLOv8 Engine   │  <-- Object detection & tensor processing
    └───────────────────┘
              │
              ▼
    ┌───────────────────┐
    │   Tracking Layer  │  <-- (Phase 2: Assigning IDs to objects)
    └───────────────────┘
              │
              ▼
    ┌───────────────────┐
    │    Visualizer     │  <-- Drawing bounding boxes & performance text
    └───────────────────┘

📂 Project LayoutPlaintextomnitrack-ai-platform/
├── inference/                  # All core backend logic lives here
│   ├── main.py                 # Application launcher
│   ├── detector.py             # YOLOv8 wrapper & model inference
│   ├── frame_source.py         # Handles camera/video input streams
│   ├── visualizer.py           # Handles UI rendering and text overlays
│   ├── config.py               # Global settings & app configurations
│   ├── logger.py               # Custom structured logger
│   ├── types.py                # Type hinting and custom dataclasses
│   └── __init__.py
├── PROJECT_DOCS/               # Deep dives into system design
│   ├── SYSTEM_ARCHITECTURE.md
│   ├── BENCHMARKS.md
│   ├── ROADMAP.md
│   └── INTERVIEW_QA.md
├── docker-compose.yml          # Container configuration
├── requirements.txt            # Project dependencies
└── README.md

📊 BenchmarksCurrent baseline metrics running on a local setup. For deep dives, check out BENCHMARKS.md.MetricSpecs / PerformanceModelYOLOv8n (Nano)Inference DeviceCPUAverage Speed~9 - 10 FPSLatency~154msDependenciesUltralytics,
OpenCV💡 Note: Running on CPU is a bottleneck. Moving inference to an NVIDIA GPU with TensorRT optimization (Phase 5) will drop latency down to single-digit milliseconds.
🛠️ Getting Started
Prerequisites
Python 3.10 or higher
A working webcam or an RTSP video link1. Clone the projectBashgit clone [https://github.com/adityaa1912/omnitrack-ai-platform.git](https://github.com/adityaa1912/omnitrack-ai-platform.git)
cd omnitrack-ai-platform
2. Set up a virtual environmentBash# Create it
python -m venv venv

# Windows activate:
venv\Scripts\activate

# Mac/Linux activate:
source venv/bin/activate
3. Install dependenciesBashpip install --upgrade pip
pip install -r requirements.txt
4. Run the codeBashpython -m inference.main
Press q while focusing on the video window to exit the program safely.
🛠️ Tech Stack
Core AI: YOLOv8, PyTorch
Computer Vision: OpenCV
Backend API (Planned): FastAPI, Uvicorn
Data Ingestion (Planned): Apache Kafka
Caching (Planned): Redis
DevOps (Planned): Docker, Kubernetes, Prometheus, Grafana
🗺️ RoadmapPhase
1: Core Foundation [DONE][x] Write modular, clean code structure instead of a single massive file[x] Real-time video window rendering with live FPS counter[x] Fix resource cleanup hooks to prevent system crashes on exitPhase
2: Tracking and Streams [IN PROGRESS][ ] Add ByteTrack and BoT-SORT algorithms[ ] Support tracking the paths of objects over time[ ] Run inference on multiple video inputs at the same timePhase
3: API & Web Tier[ ] Wrap the pipeline in FastAPI[ ] Send bounding box coordinate data via WebSockets as raw JSONPhase
4: Scaling Up[ ] Put a Kafka broker in front of the pipeline to buffer video frames[ ] Use Redis to sync object tracker states across multiple workersPhase
5: Hardware Optimization[ ] Convert the PyTorch weights to TensorRT execution engines for fast GPU inference[ ] Package everything into Kubernetes manifests with resource scaling rules👤
Author
Aditya Mengawade
GitHub: @adityaa1912
📄License
This project is currently under active development. All rights reserved.
