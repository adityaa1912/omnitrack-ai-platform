from __future__ import annotations

from typing import Any, Dict

from .errors import ConfigurationError

_SECRET_FIELDS = frozenset({
    "jwt_secret",
    "api_key",
    "alert_webhook_url",
})

_REDACTED = "***"


def validate_production_config(settings: Any) -> None:
    env = getattr(settings, "environment", "development")
    if env != "production":
        return

    errors: list[str] = []

    jwt_secret = getattr(settings, "jwt_secret", None)
    if not jwt_secret:
        errors.append("OMNITRACK_JWT_SECRET is required in production")

    cors = getattr(settings, "cors_origins", "*")
    if not cors or cors.strip() == "*":
        errors.append(
            "OMNITRACK_CORS_ORIGINS must be explicitly set in production"
        )

    postgres_url = getattr(settings, "postgres_url", None)
    if postgres_url is None:
        errors.append("OMNITRACK_POSTGRES_URL is required in production")

    logging_level = getattr(settings, "logging_level", "INFO")
    if logging_level == "DEBUG":
        errors.append(
            "OMNITRACK_LOGGING_LEVEL=DEBUG is unsafe in production"
        )

    if errors:
        raise ConfigurationError(
            "Production configuration is invalid: " + "; ".join(errors)
        )


def safe_effective_config(settings: Any) -> Dict[str, Any]:
    try:
        raw: Dict[str, Any] = settings.model_dump()
    except Exception:
        raw = {}

    for key in list(raw.keys()):
        if key in _SECRET_FIELDS and raw[key] is not None:
            raw[key] = _REDACTED
            continue
        lower = key.lower()
        if any(s in lower for s in ("password", "secret", "token")) and key not in _SECRET_FIELDS:
            if raw[key] is not None:
                raw[key] = _REDACTED

    return raw
