"""JSON-serializing cache helper over a :class:`RedisClient`.

Every method is a safe no-op (returning ``None``/``False``) when the client is
``None`` (caching disabled) or when Redis raises, so callers never need to
handle cache failures — a cache miss or outage just falls through to the
source of truth.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import redis

from ..observability.logging import get_logger
from .client import RedisClient

logger = get_logger(__name__, component="backend.cache")


class JsonCache:
    """Namespaced JSON key/value cache with TTL support."""

    def __init__(
        self,
        client: Optional[RedisClient],
        *,
        namespace: str = "omnitrack",
        default_ttl_seconds: int = 5,
    ) -> None:
        self._client = client
        self._namespace = namespace
        self._default_ttl = default_ttl_seconds

    @property
    def available(self) -> bool:
        """Whether a live Redis client is configured."""
        return self._client is not None

    def _key(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    def get(self, key: str) -> Optional[Any]:
        """Return the decoded JSON value for ``key``, or ``None`` on miss/error."""
        if self._client is None:
            return None
        try:
            raw = self._client.client.get(self._key(key))
            if raw is None:
                return None
            return json.loads(raw)
        except (redis.RedisError, ValueError) as exc:
            logger.warning(f"Cache get failed for {key}: {exc}")
            return None

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> bool:
        """Serialize ``value`` to JSON and store it with a TTL. Returns success."""
        if self._client is None:
            return False
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        try:
            self._client.client.set(self._key(key), json.dumps(value), ex=ttl)
            return True
        except (redis.RedisError, TypeError, ValueError) as exc:
            logger.warning(f"Cache set failed for {key}: {exc}")
            return False

    def delete(self, key: str) -> bool:
        """Delete ``key``. Returns True if a key was removed."""
        if self._client is None:
            return False
        try:
            return bool(self._client.client.delete(self._key(key)))
        except redis.RedisError as exc:
            logger.warning(f"Cache delete failed for {key}: {exc}")
            return False

    def exists(self, key: str) -> bool:
        """Return whether ``key`` exists."""
        if self._client is None:
            return False
        try:
            return bool(self._client.client.exists(self._key(key)))
        except redis.RedisError as exc:
            logger.warning(f"Cache exists failed for {key}: {exc}")
            return False

    def expire(self, key: str, ttl_seconds: int) -> bool:
        """Set a TTL on an existing ``key``. Returns True if applied."""
        if self._client is None:
            return False
        try:
            return bool(self._client.client.expire(self._key(key), ttl_seconds))
        except redis.RedisError as exc:
            logger.warning(f"Cache expire failed for {key}: {exc}")
            return False
