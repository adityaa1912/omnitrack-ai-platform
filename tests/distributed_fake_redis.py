"""In-memory Redis stand-in exercising the exact command subset the ownership
layer uses (SET NX EX, GET, DEL, Lua eval for compare-and-delete and
compare-and-pexpire). Thread-safe; time-travels via a settable clock so TTL
expiry tests run instantly."""

from __future__ import annotations

import threading
import time

import redis


class FakeRedis:
    def __init__(self, clock=None):
        self._store: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._clock = clock or time.time
        self.fail_mode = False

    def _check(self):
        if self.fail_mode:
            raise redis.RedisError("fake redis unavailable")

    def set(self, key, value, nx=False, ex=None, px=None):
        self._check()
        ttl = ex if ex is not None else (px / 1000.0 if px is not None else None)
        with self._lock:
            current, expires_at = self._store.get(key, (None, 0.0))
            now = self._clock()
            if current is not None and expires_at > now:
                if nx:
                    return None
            self._store[key] = (str(value), (now + ttl) if ttl else float("inf"))
            return True

    def get(self, key):
        self._check()
        with self._lock:
            current, expires_at = self._store.get(key, (None, 0.0))
            if current is None or expires_at <= self._clock():
                return None
            return current

    def delete(self, key):
        self._check()
        with self._lock:
            return 1 if self._store.pop(key, None) is not None else 0

    def ttl(self, key):
        self._check()
        with self._lock:
            current, expires_at = self._store.get(key, (None, 0.0))
            if current is None or expires_at <= self._clock():
                return -2
            return int(expires_at - self._clock())

    def eval(self, script, numkeys, *args):
        self._check()
        key, token = args[0], args[1]
        current = self.get(key)
        if current != token:
            return 0
        if "pexpire" in script:
            with self._lock:
                value, _ = self._store[key]
                self._store[key] = (value, self._clock() + int(args[2]) / 1000.0)
            return 1
        return self.delete(key)

    # Aliases used by redis-py call style differences.
    def pexpire(self, key, ms):
        self._check()
        with self._lock:
            current, expires_at = self._store.get(key, (None, 0.0))
            if current is None:
                return 0
            self._store[key] = (current, self._clock() + ms / 1000.0)
            return 1


class FakeClock:
    def __init__(self):
        self._now = 1000.0

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now += seconds
