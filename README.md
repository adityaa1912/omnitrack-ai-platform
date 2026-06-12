
# OmniTrack AI Platform

<p align="center">
  <b>Realtime AI video analytics platform for object detection, multi-object tracking, telemetry, and live operational monitoring.</b>
</p>

<p align="center">

![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)
![YOLOv8](https://img.shields.io/badge/YOLOv8-Ultralytics-00FFFF?style=for-the-badge)
![FastAPI](https://img.shields.io/badge/FastAPI-WebSockets-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-20232A?style=for-the-badge&logo=react&logoColor=61DAFB)
![TypeScript](https://img.shields.io/badge/TypeScript-Strict-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![Status](https://img.shields.io/badge/Status-Active_Development-orange?style=for-the-badge)

</p>

---

# Overview

OmniTrack is a full-stack realtime AI video analytics system designed for live object detection, multi-object tracking, stream orchestration, telemetry monitoring, and operational visualization.

The platform combines a high-performance Python inference backend with a modern React + TypeScript frontend to deliver low-latency AI monitoring through WebSocket streaming, realtime canvas rendering, and isolated telemetry pipelines.

Unlike conventional object detection demos, OmniTrack is structured as a production-style AI operations platform with emphasis on:
- bounded-memory realtime rendering
- websocket lifecycle resilience
- concurrent stream orchestration
- isolated state pipelines
- low-overhead rendering architecture
- operational telemetry systems

---

# Core Features

## Realtime AI Inference
- YOLOv8 object detection
- Multi-object tracking
- Persistent track IDs
- Bounding box visualization
- Confidence overlays
- Live inference streaming

## Realtime Transport Layer
- WebSocket-based frame streaming
- Automatic reconnect handling
- Stale stream recovery
- Latest-frame-only delivery pipeline
- Typed API contracts

## AI Operations Dashboard
- Multi-stream monitoring
- Live telemetry panels
- FPS / latency / throughput metrics
- Stream lifecycle management
- Responsive operational dashboard
- Realtime canvas overlays

## Rendering Architecture
- requestAnimationFrame-gated rendering
- createImageBitmap decoding pipeline
- Canvas-based overlay engine
- Zustand realtime stores
- React isolated from frame hot-path
- Bounded-memory rendering model

## Backend Infrastructure
- FastAPI inference backend
- SQLAlchemy persistence layer
- Concurrent stream execution
- Thread-safe database handling
- Detection persistence
- Swagger API documentation

---

# System Architecture

```text
                [ Webcam / RTSP / Video Stream ]
                                  │
                                  ▼
                   ┌──────────────────────────┐
                   │   OpenCV Frame Capture   │
                   └──────────────────────────┘
                                  │
                                  ▼
                   ┌──────────────────────────┐
                   │     YOLOv8 Inference     │
                   │ Detection & Recognition  │
                   └──────────────────────────┘
                                  │
                                  ▼
                   ┌──────────────────────────┐
                   │ Multi-Object Tracking    │
                   │ Persistent Track IDs     │
                   └──────────────────────────┘
                                  │
                                  ▼
                   ┌──────────────────────────┐
                   │ FastAPI + WebSockets     │
                   │ Realtime Transport Layer │
                   └──────────────────────────┘
                                  │
                                  ▼
                   ┌──────────────────────────┐
                   │ Zustand + Query Stores   │
                   │ Isolated State Pipelines │
                   └──────────────────────────┘
                                  │
                                  ▼
                   ┌──────────────────────────┐
                   │ Canvas Rendering Engine  │
                   │ Live Overlay Rendering   │
                   └──────────────────────────┘
                                  │
                                  ▼
                   ┌──────────────────────────┐
                   │ Telemetry & Monitoring   │
                   │ Operational Dashboard    │
                   └──────────────────────────┘
````

---

# Realtime Rendering Pipeline

OmniTrack isolates realtime rendering from the React reconciliation cycle to prevent frame-driven rerender bottlenecks.

Key architectural decisions:

* React never rerenders per incoming frame
* WebSocket transport is isolated from rendering
* Rendering is gated through requestAnimationFrame
* Latest-frame-only delivery prevents memory buildup
* createImageBitmap is used for efficient frame decoding
* Telemetry and rendering pipelines are fully separated
* Zustand selectors isolate update domains
* Canvas rendering bypasses React hot-path overhead

This enables stable realtime rendering under concurrent stream workloads while maintaining bounded memory usage.

---

# Tech Stack

| Layer            | Technologies                            |
| ---------------- | --------------------------------------- |
| Frontend         | React 18, TypeScript, Vite, TailwindCSS |
| State Management | Zustand, TanStack Query                 |
| Visualization    | Canvas API, Recharts                    |
| Backend          | FastAPI, WebSockets, SQLAlchemy         |
| AI / ML          | YOLOv8, PyTorch                         |
| Computer Vision  | OpenCV                                  |
| Database         | SQLite                                  |

---

# Current Capabilities

OmniTrack currently supports:

* realtime object detection
* multi-object tracking
* websocket video streaming
* live telemetry visualization
* concurrent stream management
* operational AI dashboards
* resilient reconnect handling
* realtime overlay rendering

The platform can function as:

* AI surveillance system
* realtime monitoring dashboard
* computer vision experimentation platform
* AI operations interface
* smart analytics system

---

# Performance Characteristics

| Metric           | Value                    |
| ---------------- | ------------------------ |
| Model            | YOLOv8n                  |
| Inference Device | CPU                      |
| Average FPS      | ~5–10 FPS                |
| Transport        | WebSockets               |
| Rendering        | Canvas + rAF             |
| Frontend State   | Zustand + TanStack Query |

---

# Project Structure

```text
omnitrack-ai-platform/
│
├── backend/
│   ├── app.py
│   ├── service.py
│   ├── models.py
│   └── run.py
│
├── inference/
│   ├── detector.py
│   ├── frame_source.py
│   ├── tracker.py
│   ├── visualizer.py
│   ├── tracking_types.py
│   ├── config.py
│   └── types.py
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── store/
│   │   ├── lib/
│   │   ├── pages/
│   │   └── types/
│
├── requirements.txt
├── docker-compose.yml
└── README.md
```

---

# Installation

## Clone Repository

```bash
git clone https://github.com/adityaa1912/omnitrack-ai-platform.git

cd omnitrack-ai-platform
```

---

# Backend Setup

## Create Virtual Environment

```bash
python -m venv venv
```

---

## Activate Environment

### Windows

```bash
venv\Scripts\activate
```

### Linux / Mac

```bash
source venv/bin/activate
```

---

## Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Start Backend

```bash
python -m backend.run
```

Backend:

```text
http://127.0.0.1:8000
```

Swagger Docs:

```text
http://127.0.0.1:8000/docs
```

---

# Frontend Setup

```bash
cd frontend

npm install

npm run dev
```

Frontend:

```text
http://localhost:5173
```

---

# Example Stream Configuration

```json
{
  "stream_id": "omnitrack-live",
  "source": 0,
  "width": 640,
  "height": 480,
  "fps": 20,
  "confidence_threshold": 0.45,
  "tracking_enabled": true,
  "track_distance": 40,
  "max_age": 15
}
```

---

# Roadmap

## Completed

* YOLOv8 realtime inference
* Multi-object tracking
* FastAPI backend
* WebSocket transport
* React operations dashboard
* Realtime telemetry system
* Canvas rendering engine
* Stream lifecycle management

## Planned

* Historical replay system
* Collision detection
* Event alert pipelines
* Kafka streaming
* Redis synchronization
* PostgreSQL migration
* GPU acceleration
* Docker deployment
* Kubernetes orchestration

---

# Use Cases

* Smart surveillance systems
* Traffic analytics
* Industrial monitoring
* Warehouse systems
* Retail analytics
* Edge AI experimentation
* Multi-camera monitoring

---

# Author

**Aditya Mengawade**

GitHub:
https://github.com/adityaa1912

---

# License

MIT License

```
```
