from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_compose_topology_preserves_single_backend_owner() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"backend", "frontend"}
    assert compose["services"]["backend"]["restart"] == "unless-stopped"
    assert compose["services"]["backend"]["volumes"] == ["backend-data:/app/data"]
    assert compose["services"]["frontend"]["depends_on"]["backend"]["condition"] == "service_healthy"
    assert compose["volumes"]["backend-data"]["name"] == "omnitrack-backend-data"
    assert compose["networks"]["omnitrack"]["driver"] == "bridge"
    assert compose["networks"]["omnitrack"]["name"] == "omnitrack-network"


def test_compose_defaults_to_sqlite_with_opt_in_external_services() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["backend"]["environment"]

    assert env["OMNITRACK_POSTGRES_URL"] == "${OMNITRACK_POSTGRES_URL:-}"
    assert env["OMNITRACK_REDIS_ENABLED"] == "${OMNITRACK_REDIS_ENABLED:-false}"
    assert env["OMNITRACK_REDIS_URL"] == "${OMNITRACK_REDIS_URL:-}"
    assert env["OMNITRACK_KAFKA_ENABLED"] == "${OMNITRACK_KAFKA_ENABLED:-false}"
    assert env["OMNITRACK_KAFKA_BOOTSTRAP_SERVERS"] == "${OMNITRACK_KAFKA_BOOTSTRAP_SERVERS:-}"


def test_k8s_backend_configmap_sets_explicit_cors_origins() -> None:
    import yaml

    manifest = yaml.safe_load((ROOT / "kubernetes" / "backend-configmap.yaml").read_text(encoding="utf-8"))
    data = manifest["data"]

    assert "OMNITRACK_CORS_ORIGINS" in data
    assert data["OMNITRACK_CORS_ORIGINS"] != "*"


def test_frontend_proxy_preserves_rest_and_websocket_contracts() -> None:
    config = (ROOT / "docker" / "nginx.conf.template").read_text(encoding="utf-8")

    assert "location /api/" in config
    assert "location /ws/" in config
    assert "proxy_set_header Upgrade $http_upgrade;" in config
    assert "proxy_pass http://${OMNITRACK_BACKEND_HOST}:${OMNITRACK_API_PORT}/;" in config
