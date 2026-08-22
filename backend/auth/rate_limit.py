"""Rate limiting for authentication endpoints."""

import time
from collections import defaultdict
from typing import Dict, Tuple
import threading

from fastapi import HTTPException, Request, status


class RateLimiter:
    """Simple in-memory rate limiter for auth endpoints.

    Tracks request counts per IP with a sliding window.
    Not suitable for distributed deployments (use Redis for that).
    """

    def __init__(self, requests_per_minute: int = 10):
        self.requests_per_minute = requests_per_minute
        self._requests: Dict[str, list] = defaultdict(list)
        self._lock = threading.Lock()

    def _cleanup_old(self, ip: str, now: float) -> None:
        """Remove requests older than 60 seconds."""
        cutoff = now - 60
        self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]

    def check(self, ip: str) -> None:
        """Raise HTTPException if rate limit exceeded."""
        if self.requests_per_minute <= 0:
            return  # Disabled

        now = time.time()
        with self._lock:
            self._cleanup_old(ip, now)
            count = len(self._requests[ip])
            if count >= self.requests_per_minute:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please try again later.",
                    headers={"Retry-After": "60"},
                )
            self._requests[ip].append(now)

    def get_remaining(self, ip: str) -> int:
        """Return remaining requests allowed in current window."""
        now = time.time()
        with self._lock:
            self._cleanup_old(ip, now)
            return max(0, self.requests_per_minute - len(self._requests[ip]))


# Global rate limiter instance
_auth_rate_limiter: RateLimiter = None


def reset_auth_rate_limiter() -> None:
    """Reset the global rate limiter state (used in tests for isolation)."""
    global _auth_rate_limiter
    _auth_rate_limiter = None


def get_auth_rate_limiter() -> RateLimiter:
    """Get or create the auth rate limiter."""
    global _auth_rate_limiter
    if _auth_rate_limiter is None:
        from backend.settings import get_settings
        settings = get_settings()
        _auth_rate_limiter = RateLimiter(settings.auth_rate_limit_per_minute)
    return _auth_rate_limiter


async def rate_limit_auth(request: Request) -> None:
    """Dependency: rate limit auth endpoints by IP."""
    ip = request.client.host if request.client else "unknown"
    get_auth_rate_limiter().check(ip)