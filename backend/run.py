#!/usr/bin/env python
"""
Entry point for running the FastAPI backend server.

Usage:
    python -m backend.run --host 0.0.0.0 --port 8000
    python -m backend.run --reload  # Development mode with auto-reload
"""

import argparse
import logging
import os

import uvicorn

from backend.settings import get_settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _run_alembic_upgrade() -> None:
    """Bring the database schema up to date before the app starts.

    Runs ``alembic upgrade head`` so the running image converges to the latest
    schema without a separate operator step. For SQLite (default) this is a
    local file migration; for PostgreSQL it migrates the configured database.
    Failures here are non-fatal at startup so local development isn't blocked
    by a missing migration environment — the app still starts and can
    ``create_all`` convenience tables instead.
    """
    try:
        from alembic.config import Config
        from alembic import command

        ini = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
        cfg = Config(ini)
        cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
        command.upgrade(cfg, "head")
    except Exception as exc:  # noqa: BLE001 - migrations are best-effort at startup
        logger.warning("Alembic upgrade skipped: %s", exc)


def main():
    """Run FastAPI backend server."""
    settings = get_settings()
    parser = argparse.ArgumentParser(description="YOLOv8 Inference API Backend")
    parser.add_argument("--host", default=settings.api_host, help="API bind host")
    parser.add_argument("--port", type=int, default=settings.api_port, help="API bind port")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload (development)")
    parser.add_argument(
        "--workers",
        type=int,
        choices=(1,),
        default=settings.api_workers,
        help="Number of worker processes (must be 1 while stream state is local)",
    )
    parser.add_argument("--db", default=settings.sqlite_path, help="SQLite database file path")

    args = parser.parse_args()

    # Run Alembic migrations before starting the app so the schema converges
    # to the latest revision without a separate operator step.
    _run_alembic_upgrade()

    # Preserve the existing --db CLI contract while keeping backend.main and
    # direct Uvicorn startup on the same validated settings path.
    os.environ["OMNITRACK_SQLITE_PATH"] = args.db
    get_settings.cache_clear()
    from backend.main import app

    logger.info(f"Starting YOLOv8 Inference API on {args.host}:{args.port}")
    logger.info(f"API Documentation: http://{args.host}:{args.port}/docs")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1 if args.reload else settings.api_workers,
        log_level=settings.logging_level.lower(),
    )


if __name__ == "__main__":
    main()
