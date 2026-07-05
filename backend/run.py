#!/usr/bin/env python
"""
Entry point for running the FastAPI backend server.

Usage:
    python -m backend.run --host 0.0.0.0 --port 8000
    python -m backend.run --reload  # Development mode with auto-reload
"""

import argparse
import logging

import uvicorn

from backend.main import app


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Run FastAPI backend server."""
    parser = argparse.ArgumentParser(description="YOLOv8 Inference API Backend")
    parser.add_argument("--host", default="0.0.0.0", help="Server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Server port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (development)")
    parser.add_argument("--workers", type=int, default=1, help="Number of worker processes")
    parser.add_argument("--db", default="inference_data.db", help="Database file path")

    args = parser.parse_args()

    logger.info(f"Starting YOLOv8 Inference API on {args.host}:{args.port}")
    logger.info(f"API Documentation: http://{args.host}:{args.port}/docs")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1 if args.reload else args.workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()
