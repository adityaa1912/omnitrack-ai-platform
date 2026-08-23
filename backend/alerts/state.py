from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from ..cache.json_cache import JsonCache


class AlertStateStore:
    def __init__(
        self,
        json_cache: Optional[JsonCache] = None,
        *,
        dedup_window_seconds: int = 300,
    ) -> None:
        self._cache = json_cache
        self._dedup_window = dedup_window_seconds
        self._lock = threading.Lock()
        self._cooldowns: Dict[str, float] = {}
        self._dedup: Dict[str, float] = {}

    def _purge_locked(self, now: float) -> None:
        expired = [k for k, exp in self._cooldowns.items() if exp <= now]
        for k in expired:
            self._cooldowns.pop(k, None)
        expired = [k for k, exp in self._dedup.items() if exp <= now]
        for k in expired:
            self._dedup.pop(k, None)

    def _cooldown_key(self, rule_id: Any, dedup_key: str) -> str:
        return f"alerts:cooldown:{rule_id}:{dedup_key}"

    def _dedup_key(self, rule_id: Any, dedup_key: str) -> str:
        return f"alerts:dedup:{rule_id}:{dedup_key}"

    def in_cooldown(self, rule_id: Any, dedup_key: str) -> bool:
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            if self._cooldowns.get(self._cooldown_key(rule_id, dedup_key), 0) > now:
                return True
        if self._cache is not None:
            return self._cache.exists(self._cooldown_key(rule_id, dedup_key))
        return False

    def set_cooldown(self, rule_id: Any, dedup_key: str, cooldown_seconds: int) -> None:
        if cooldown_seconds <= 0:
            return
        key = self._cooldown_key(rule_id, dedup_key)
        with self._lock:
            self._cooldowns[key] = time.time() + cooldown_seconds
        if self._cache is not None:
            self._cache.set(key, 1, ttl_seconds=cooldown_seconds)

    def is_duplicate(self, rule_id: Any, dedup_key: str) -> bool:
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            if self._dedup.get(self._dedup_key(rule_id, dedup_key), 0) > now:
                return True
        if self._cache is not None:
            return self._cache.exists(self._dedup_key(rule_id, dedup_key))
        return False

    def mark_seen(self, rule_id: Any, dedup_key: str, window_seconds: Optional[int] = None) -> None:
        window = window_seconds if window_seconds is not None else self._dedup_window
        if window <= 0:
            return
        key = self._dedup_key(rule_id, dedup_key)
        with self._lock:
            self._dedup[key] = time.time() + window
        if self._cache is not None:
            self._cache.set(key, 1, ttl_seconds=window)

    def cache_active(self, stream_id: str, alerts: List[Dict[str, Any]], ttl_seconds: int = 30) -> None:
        if self._cache is not None:
            self._cache.set(f"alerts:active:{stream_id}", alerts, ttl_seconds=ttl_seconds)

    def get_active(self, stream_id: str) -> Optional[List[Dict[str, Any]]]:
        if self._cache is not None:
            return self._cache.get(f"alerts:active:{stream_id}")
        return None
