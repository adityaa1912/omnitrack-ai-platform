# OmniTrack AI Platform

<p align="center">
  <b>Production-grade real-time object detection and tracking platform built using YOLOv8, modular AI inference pipelines, and scalable distributed system architecture.</b>
</p>

<p align="center">

![Python](https://img.shields.io/badge/Python-3.11-blue)
![YOLOv8](https://img.shields.io/badge/YOLOv8-Ultralytics-green)
![OpenCV](https://img.shields.io/badge/OpenCV-ComputerVision-red)
![Status](https://img.shields.io/badge/Status-ActiveDevelopment-orange)

</p>

---

# 👁️ Overview

OmniTrack is a modular AI-powered surveillance and analytics platform designed for real-time object detection, tracking, and scalable distributed inference.

Unlike basic demo scripts, this project is engineered as a production-oriented AI infrastructure system with clean architecture, abstraction layers, extensibility, and deployment readiness.

The platform is being developed incrementally toward:
- multi-object tracking
- distributed stream ingestion
- scalable inference orchestration
- GPU acceleration
- Kubernetes-native deployment

---

# 🚀 Features

## ✅ Currently Implemented

- Real-time YOLOv8 object detection
- Webcam video stream inference
- Bounding box rendering
- Confidence score visualization
- FPS monitoring
- Structured logging system
- Graceful shutdown handling
- Modular architecture
- Production-style code organization
- Type-safe dataclass-driven pipeline

---

## 🔥 Planned Features

- ByteTrack / BoT-SORT integration
- Persistent object IDs
- Multi-object trajectory analysis
- RTSP stream ingestion
- Multi-stream concurrent inference
- Kafka-based distributed ingestion
- FastAPI inference APIs
- Redis state synchronization
- TensorRT optimization
- Kubernetes deployment
- Distributed GPU workers
- Prometheus/Grafana monitoring

---

# 🏗️ System Architecture

The system separates:
- frame ingestion
- model inference
- tracking
- visualization
- orchestration

This ensures future scalability without major architectural rewrites.

```text
         [ Webcam / RTSP Stream ]
                     │
                     ▼
        ┌────────────────────────┐
        │   Frame Ingestion      │
        │   OpenCV Video Layer   │
        └────────────────────────┘
                     │
                     ▼
        ┌────────────────────────┐
        │    YOLOv8 Engine       │
        │ Detection & Inference  │
        └────────────────────────┘
                     │
                     ▼
        ┌────────────────────────┐
        │   Tracking Layer       │
        │ ByteTrack / BoT-SORT   │
        └────────────────────────┘
                     │
                     ▼
        ┌────────────────────────┐
        │     Visualization      │
        │ Rendering + Metrics    │
        └────────────────────────┘
                     │
                     ▼
        ┌────────────────────────┐
        │ Future Distributed     │
        │ Services & APIs        │
        └────────────────────────┘
```

---

# 📂 Project Structure

```text
omnitrack-ai-platform/
│
├── inference/
│   ├── main.py
│   ├── detector.py
│   ├── frame_source.py
│   ├── visualizer.py
│   ├── config.py
│   ├── logger.py
│   ├── types.py
│   └── __init__.py
│
├── PROJECT_DOCS/
│   ├── SYSTEM_ARCHITECTURE.md
│   ├── BENCHMARKS.md
│   ├── ROADMAP.md
│   └── INTERVIEW_QA.md
│
├── docker-compose.yml
├── requirements.txt
├── .gitignore
└── README.md
```

---

# 📊 Performance Benchmarks

| Metric | Value |
|---|---|
| Model | YOLOv8n |
| Inference Device | CPU |
| Average FPS | ~9-10 FPS |
| Inference Latency | ~154ms |
| Framework | Ultralytics + OpenCV |

> ⚠️ Current inference is CPU-bound. Future GPU acceleration using TensorRT and CUDA optimization is planned.

---

# 🛠️ Getting Started

## Prerequisites

- Python 3.10+
- Webcam or RTSP stream
- Git installed

---

## 1️⃣ Clone Repository

```bash
git clone https://github.com/adityaa1912/omnitrack-ai-platform.git
cd omnitrack-ai-platform
```

---

## 2️⃣ Create Virtual Environment

```bash
python -m venv venv
```

---

## 3️⃣ Activate Environment

### Windows

```bash
venv\Scripts\activate
```

### Linux / Mac

```bash
source venv/bin/activate
```

---

## 4️⃣ Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 5️⃣ Run the Inference Pipeline

```bash
python -m inference.main
```

Press:

```text
q
```

to exit safely.

---

# 🛠️ Technology Stack

| Layer | Technologies |
|---|---|
| AI/ML | YOLOv8, PyTorch |
| Computer Vision | OpenCV |
| Backend (Planned) | FastAPI |
| Streaming (Planned) | Apache Kafka |
| Caching (Planned) | Redis |
| Deployment (Planned) | Docker, Kubernetes |
| Monitoring (Planned) | Prometheus, Grafana |

---

# 🎯 Engineering Goals

- Modular architecture
- Low-latency inference
- Scalable distributed design
- Production-oriented maintainability
- Multi-stream processing support
- GPU acceleration readiness
- Clean observability and monitoring

---

# 🗺️ Development Roadmap

## ✅ Phase 1: Core Detection Pipeline

- [x] Modular inference architecture
- [x] Real-time webcam inference
- [x] FPS monitoring
- [x] Structured logging
- [x] Graceful shutdown handling

---

## 🚧 Phase 2: Multi-Object Tracking

- [ ] ByteTrack integration
- [ ] Persistent tracking IDs
- [ ] Trajectory rendering
- [ ] Object lifecycle management

---

## 🔌 Phase 3: API & Streaming Layer

- [ ] FastAPI inference server
- [ ] WebSocket streaming
- [ ] Async processing pipeline

---

## 🌐 Phase 4: Distributed Infrastructure

- [ ] Kafka ingestion pipeline
- [ ] Redis synchronization
- [ ] Multi-stream orchestration
- [ ] Distributed worker system

---

## ⚡ Phase 5: Hardware Optimization

- [ ] TensorRT conversion
- [ ] CUDA acceleration
- [ ] GPU inference workers
- [ ] Kubernetes deployment

---

# 📈 Future Enterprise Use Cases

- Smart surveillance systems
- Traffic analytics
- Retail customer analytics
- Industrial safety monitoring
- Crowd anomaly detection
- Smart city orchestration
- Warehouse automation

---

# 👨‍💻 Author

**Aditya Mengawade**

GitHub:  
https://github.com/adityaa1912

---

# 📄 License

This project is currently under active development.

All rights reserved.
