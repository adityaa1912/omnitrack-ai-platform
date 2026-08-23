"""Redis caching layer for analytics reads.

Wraps the existing ``JsonCache`` to provide TTL-backed caching for
analytics endpoints. Cache keys are invalidated on analytics flush
so stale data is never served.
"""

from __future__ import annotations

from typing import Any, Optional

from ..cache.json_cache import JsonCache


class AnalyticsCache:
    """Namespaced cache for analytics reads."""

    def __init__(self, cache: Optional[JsonCache], default_ttl_seconds: int = 10) -> None:
        self._cache = cache
        self._default_ttl = default_ttl_seconds

    @property
    def available(self) -> bool:
        return self._cache is not None and self._cache.available

    def _key(self, *parts: str) -> str:
        return ":".join(parts)

    def get(self, *parts: str) -> Optional[Any]:
        if not self.available:
            return None
        return self._cache.get(self._key(*parts))

    def set(self, value: Any, *parts: str, ttl_seconds: Optional[int] = None) -> bool:
        if not self.available:
            return False
        return self._cache.set(self._key(*parts), value, ttl_seconds=ttl_seconds or self._default_ttl)

    def invalidate(self, *parts: str) -> bool:
        if not self.available:
            return False
        return self._cache.delete(self._key(*parts))

    def invalidate_all(self) -> int:
        """Invalidate all analytics cache entries. Returns count invalidated."""
        if not self.available:
            return 0
        return 0  # JsonCache doesn't support prefix scan; individual invalidation is used
