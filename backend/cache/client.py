"""Redis client factory with pooling, health check, and graceful shutdown.

The client is optional: when Redis is disabled or unreachable the factory
returns an object whose ``available`` is ``False`` and every operation is a
safe no-op, so the backend runs unchanged without Redis.
"""

from __future__ import annotations

import threading
from typing import Optional

import redis

from ..observability.logging import get_logger

logger = get_logger(__name__, component="backend.cache")


class RedisClient:
    """Thin wrapper around a pooled ``redis.Redis`` connection.

    Connection pooling is handled by the ``redis`` client itself (it owns a
    ``ConnectionPool`` sized by ``max_connections``). Automatic reconnect is
    inherent: each operation checks out a pooled connection and the pool
    re-establishes dropped connections on demand, so a transient Redis restart
    does not require client recreation.
    """

    def __init__(
        self,
        url: str,
        *,
        max_connections: int = 10,
        socket_timeout: float = 2.0,
        socket_connect_timeout: float = 2.0,
    ) -> None:
        self._url = url
        self._lock = threading.Lock()
        self._closed = False
        self._client: redis.Redis = redis.Redis.from_url(
            url,
            max_connections=max_connections,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            # Decode to str so the JSON helper works on text, not bytes.
            decode_responses=True,
        )

    @property
    def client(self) -> redis.Redis:
        return self._client

    def ping(self) -> bool:
        """Health check: return True iff Redis answers PING."""
        try:
            return bool(self._client.ping())
        except redis.RedisError as exc:
            logger.warning(f"Redis health check failed: {exc}")
            return False

    def close(self) -> None:
        """Gracefully close the pool. Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._client.close()
            except redis.RedisError as exc:  # noqa: BLE001 - shutdown is resilient
                logger.warning(f"Redis close error (ignored): {exc}")


def create_redis_client(
    url: Optional[str],
    *,
    max_connections: int = 10,
    socket_timeout: float = 2.0,
    socket_connect_timeout: float = 2.0,
) -> Optional[RedisClient]:
    """Build a pooled Redis client, or ``None`` when caching is disabled.

    ``url`` is ``None`` when the feature flag is off (see
    ``Settings.resolved_redis_url``); returning ``None`` keeps "disabled" and
    "unreachable" distinct so readiness can report each accurately. A live
    connection is NOT established here — the pool connects lazily on first use,
    so startup never blocks on Redis.
    """
    if not url:
        return None
    return RedisClient(
        url,
        max_connections=max_connections,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_connect_timeout,
    )
