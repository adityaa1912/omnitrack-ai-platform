from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.settings import Settings


def test_environment_selects_sqlite_default() -> None:
    assert Settings(_env_file=None).sqlite_path == "inference_data.db"
    assert (
        Settings(_env_file=None, environment="container").sqlite_path
        == "/app/data/inference_data.db"
    )


def test_explicit_sqlite_path_overrides_environment_default() -> None:
    settings = Settings(
        _env_file=None,
        environment="container",
        sqlite_path="/mnt/state/omnitrack.db",
    )
    assert settings.sqlite_path == "/mnt/state/omnitrack.db"


def test_environment_variables_are_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNITRACK_API_PORT", "9010")
    monkeypatch.setenv("VITE_API_BASE_URL", "https://api.example.test")

    settings = Settings(_env_file=None)

    assert settings.api_port == 9010
    assert settings.frontend_api_url == "https://api.example.test"


def test_multiple_api_workers_are_rejected() -> None:
    with pytest.raises(ValidationError, match="must be 1"):
        Settings(_env_file=None, api_workers=2)


def test_invalid_backoff_range_is_rejected() -> None:
    with pytest.raises(ValidationError, match="BACKOFF_MAX"):
        Settings(
            _env_file=None,
            inference_backoff_initial_seconds=10,
            inference_backoff_max_seconds=1,
        )


def test_future_connection_settings_are_validated() -> None:
    settings = Settings(
        _env_file=None,
        postgres_url="postgresql://user:pass@db.example.test:5432/omnitrack",
        redis_url="redis://cache.example.test:6379/0",
        kafka_bootstrap_servers="broker-a.example.test:9092, broker-b.example.test:9092",
    )
    assert str(settings.postgres_url).startswith("postgresql://")
    assert str(settings.redis_url).startswith("redis://")
    assert settings.kafka_bootstrap_servers == (
        "broker-a.example.test:9092,broker-b.example.test:9092"
    )

    with pytest.raises(ValidationError, match="host:port"):
        Settings(_env_file=None, kafka_bootstrap_servers="broker-without-port")
