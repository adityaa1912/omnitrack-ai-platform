"""Validated runtime configuration for the OmniTrack API process.

Values come from process environment variables and, for local development,
the repository-root ``.env`` file.  Compose injects the same names into the
containers, so no deployment code needs a second configuration parser.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single, validated source of runtime settings for the current platform."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        env_prefix="OMNITRACK_",
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    environment: Literal["development", "test", "container", "production"] = (
        "development"
    )
    api_host: str = Field(default="0.0.0.0", min_length=1)
    api_port: int = Field(default=8000, ge=1, le=65535)
    api_workers: int = Field(default=1, ge=1)
    frontend_port: int = Field(default=8080, ge=1, le=65535)
    frontend_dev_port: int = Field(default=5173, ge=1, le=65535)
    backend_host: str = Field(default="backend", min_length=1)
    local_api_host: str = Field(default="127.0.0.1", min_length=1)

    sqlite_path: str | None = None
    postgres_url: PostgresDsn | None = None
    redis_url: RedisDsn | None = None
    kafka_bootstrap_servers: str | None = None

    # PostgreSQL connection pooling. Ignored for SQLite (which uses SQLAlchemy's
    # default QueuePool over a single file). ``db_pool_pre_ping`` validates a
    # pooled connection before checkout so a dropped/stale connection is
    # recycled instead of surfacing as a mid-request error.
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_recycle_seconds: int = Field(default=1800, ge=-1)
    db_pool_pre_ping: bool = True
    # Transient-connect retry at startup (PostgreSQL may still be coming up).
    db_connect_max_attempts: int = Field(default=5, ge=1)
    db_connect_retry_delay_seconds: float = Field(default=1.0, gt=0)

    # Redis cache (optional). Disabled by default so local development runs
    # without Redis; enable with ``OMNITRACK_REDIS_ENABLED=true`` and point
    # ``OMNITRACK_REDIS_URL`` at a server. When disabled or unreachable the
    # backend runs normally — caching is simply bypassed.
    redis_enabled: bool = False
    redis_socket_timeout_seconds: float = Field(default=2.0, gt=0)
    redis_socket_connect_timeout_seconds: float = Field(default=2.0, gt=0)
    redis_max_connections: int = Field(default=10, ge=1)
    # Default TTL (seconds) applied to cached entries when a caller does not
    # specify one. Short-lived: cached reads are near-real-time projections.
    redis_default_ttl_seconds: int = Field(default=5, ge=1)

    model_path: str = Field(default="yolov8n.pt", min_length=1)
    inference_device: str = Field(default="cpu", min_length=1)
    inference_source_retry_attempts: int = Field(default=3, ge=1)
    inference_source_retry_delay_seconds: float = Field(default=5.0, gt=0)
    inference_stream_timeout_seconds: float = Field(default=10.0, gt=0)
    inference_backoff_initial_seconds: float = Field(default=1.0, gt=0)
    inference_backoff_max_seconds: float = Field(default=30.0, gt=0)
    inference_backoff_factor: float = Field(default=2.0, ge=1)
    stream_stop_timeout_seconds: float = Field(default=5.0, gt=0)
    frame_queue_capacity: int = Field(default=30, ge=1)
    frame_ws_poll_timeout_seconds: float = Field(default=1.0, gt=0)
    event_buffer_capacity: int = Field(default=1000, ge=1)
    event_ws_queue_capacity: int = Field(default=100, ge=1)

    logging_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "*"
    api_key: str | None = None

    # Observability. Metrics are always exposed at /metrics (Prometheus text
    # format); the system sampler interval controls how often process CPU /
    # memory / thread gauges are refreshed off the request path.
    metrics_enabled: bool = True
    metrics_system_sample_interval_seconds: float = Field(default=5.0, gt=0)
    # Header name consulted for an incoming correlation/request id. When absent
    # or empty, a fresh id is generated. The id is always echoed back on the
    # ``X-Request-ID`` response header.
    request_id_header: str = Field(default="X-Request-ID", min_length=1)

    healthcheck_interval_seconds: int = Field(default=30, ge=1)
    healthcheck_timeout_seconds: int = Field(default=5, ge=1)
    healthcheck_start_period_seconds: int = Field(default=20, ge=0)
    healthcheck_retries: int = Field(default=3, ge=1)
    container_stop_grace_period_seconds: int = Field(default=15, ge=1)

    frontend_api_url: str = Field(
        default="/api", validation_alias="VITE_API_BASE_URL"
    )
    frontend_ws_url: str = Field(
        default="/ws", validation_alias="VITE_WS_BASE_URL"
    )

    @field_validator("api_workers")
    @classmethod
    def _single_worker_until_stream_state_is_shared(cls, value: int) -> int:
        if value != 1:
            raise ValueError(
                "OMNITRACK_API_WORKERS must be 1 while streams and WebSockets "
                "are process-local"
            )
        return value

    @field_validator("kafka_bootstrap_servers")
    @classmethod
    def _validate_kafka_bootstrap_servers(cls, value: str | None) -> str | None:
        if value is None:
            return None
        endpoints = [endpoint.strip() for endpoint in value.split(",") if endpoint.strip()]
        if not endpoints or any(":" not in endpoint for endpoint in endpoints):
            raise ValueError(
                "OMNITRACK_KAFKA_BOOTSTRAP_SERVERS must be a comma-separated "
                "host:port list"
            )
        return ",".join(endpoints)

    @field_validator("frontend_api_url")
    @classmethod
    def _validate_frontend_api_url(cls, value: str) -> str:
        if value.startswith("/") or value.startswith(("http://", "https://")):
            return value
        raise ValueError("VITE_API_BASE_URL must be an absolute path or HTTP(S) URL")

    @field_validator("frontend_ws_url")
    @classmethod
    def _validate_frontend_ws_url(cls, value: str) -> str:
        if value.startswith("/") or value.startswith(("ws://", "wss://")):
            return value
        raise ValueError("VITE_WS_BASE_URL must be an absolute path or WS(S) URL")

    @model_validator(mode="after")
    def _resolve_environment_defaults(self) -> "Settings":
        if self.sqlite_path is None:
            self.sqlite_path = (
                "/app/data/inference_data.db"
                if self.environment == "container"
                else "inference_data.db"
            )
        if self.inference_backoff_max_seconds < self.inference_backoff_initial_seconds:
            raise ValueError(
                "OMNITRACK_INFERENCE_BACKOFF_MAX_SECONDS must be greater than "
                "or equal to OMNITRACK_INFERENCE_BACKOFF_INITIAL_SECONDS"
            )
        return self

    @property
    def database_url(self) -> str:
        """Resolve the SQLAlchemy database URL for the active environment.

        PostgreSQL is used when ``OMNITRACK_POSTGRES_URL`` is set (production /
        container); otherwise the local SQLite file is used (development). This
        is the single selection point — the rest of the app only sees a URL.
        """
        if self.postgres_url is not None:
            return str(self.postgres_url)
        return f"sqlite:///{self.sqlite_path}"

    @property
    def database_is_sqlite(self) -> bool:
        """Whether the resolved database URL targets SQLite."""
        return self.database_url.startswith("sqlite")

    @property
    def resolved_redis_url(self) -> str | None:
        """Return the Redis URL when caching is enabled, else ``None``.

        Returns ``None`` when ``redis_enabled`` is false so callers can treat
        "no URL" as "caching disabled" without a separate flag check.
        """
        if not self.redis_enabled or self.redis_url is None:
            return None
        return str(self.redis_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide immutable-by-convention settings instance."""
    return Settings()
