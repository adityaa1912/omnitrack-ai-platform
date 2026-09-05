"""Redis-backed stream ownership leases for multi-replica deployments.

A lease is a single Redis key ``omnitrack:lease:<stream_id>`` holding the owner
instance id, with a TTL renewed by a per-process heartbeat thread. Acquisition
is atomic (``SET key value NX EX ttl``). The heartbeat thread stops the local
stream the moment the lease is lost (expired by a network partition, taken
over after a pod crash, or stolen by reassignment), so inference fails safe
instead of duplicating work.

All failures (Redis outage, timeouts) surface as ``LeaseLostError`` or a
``False`` acquire result — never an exception into the caller's hot path
beyond that — and ``None`` in distributed mode acts as a local no-op owner, so
single-process deployments are byte-for-byte unchanged.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Optional

import redis
from ..observability.logging import get_logger
from ..observability.metrics import (
    LEASE_ACQUISITIONS_TOTAL,
    LEASE_HEARTBEATS_TOTAL,
    LEASE_LOSSES_TOTAL,
    LEASE_OWNED_STREAMS,
    STREAM_REASSIGNMENTS_TOTAL,
)

logger = get_logger(__name__, component="backend.ownership")

LEASE_KEY_PREFIX = "omnitrack:lease:"


class LeaseLostError(RuntimeError):
    """Raised when the current process no longer owns a stream's lease."""


def _instance_identity() -> str:
    import os

    pod = os.environ.get("OMNITRACK_INSTANCE_ID") or os.environ.get("HOSTNAME")
    return f"{pod or 'local'}:{uuid.uuid4().hex[:8]}"


class StreamLease:
    """TTL lease on one stream id, owned by this process's instance id."""

    def __init__(
        self,
        stream_id: str,
        client: "redis.Redis",
        instance_id: str,
        ttl_seconds: float,
        *,
        on_lost: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.stream_id = stream_id
        self._client = client
        self._instance_id = instance_id
        self._ttl_seconds = ttl_seconds
        self._key = f"{LEASE_KEY_PREFIX}{stream_id}"
        self._on_lost = on_lost
        self._lock = threading.Lock()
        self._last_heartbeat_ok = False
        self._lost = False

    @property
    def owner(self) -> str:
        return self._instance_id

    @property
    def last_heartbeat_ok(self) -> bool:
        with self._lock:
            return self._last_heartbeat_ok

    def _mark_lost(self, reason: str) -> None:
        with self._lock:
            if self._lost:
                return
            self._lost = True
            self._last_heartbeat_ok = False
        LEASE_LOSSES_TOTAL.labels(reason=reason).inc()
        logger.error(
            f"Stream {self.stream_id} lease lost ({reason}); "
            f"stopping local inference to avoid duplicate work"
        )
        if self._on_lost is not None:
            try:
                self._on_lost(self.stream_id)
            except Exception as exc:  # noqa: BLE001 - callback must not raise
                logger.warning(
                    f"Lease-loss callback failed for {self.stream_id}: {exc}"
                )

    def acquire(self, acquire_timeout_seconds: float) -> bool:
        """Atomically acquire the lease, retrying within the timeout window."""
        deadline = time.monotonic() + max(acquire_timeout_seconds, 0.0)
        token = self._instance_id
        while True:
            try:
                acquired = bool(
                    self._client.set(
                        self._key, token, nx=True, ex=int(self._ttl_seconds)
                    )
                )
            except redis.RedisError as exc:
                logger.warning(
                    f"Redis error acquiring lease for {self.stream_id}: {exc}"
                )
                acquired = False
            if acquired:
                with self._lock:
                    self._lost = False
                    self._last_heartbeat_ok = True
                LEASE_ACQUISITIONS_TOTAL.labels(outcome="acquired").inc()
                return True
            if time.monotonic() >= deadline:
                LEASE_ACQUISITIONS_TOTAL.labels(outcome="held_elsewhere").inc()
                return False
            time.sleep(0.05)

    def release(self) -> bool:
        """Release the lease iff this instance still owns it (atomic via Lua)."""
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] "
            "then return redis.call('del', KEYS[1]) else return 0 end"
        )
        try:
            released = bool(
                self._client.eval(script, 1, self._key, self._instance_id)
            )
        except redis.RedisError as exc:
            logger.warning(
                f"Redis error releasing lease for {self.stream_id}: {exc}"
            )
            return False
        if released:
            with self._lock:
                self._lost = True
                self._last_heartbeat_ok = False
        return released

    def check_ownership(self) -> bool:
        """Verify current ownership without renewing; True when owned."""
        if self._is_lost():
            return False
        try:
            owner = self._client.get(self._key)
            if owner == self._instance_id:
                return True
            if owner is None:
                self._mark_lost("expired")
                return False
            self._mark_lost(f"owned by {owner}")
            return False
        except redis.RedisError:
            return False

    def heartbeat(self) -> bool:
        """Renew the TTL iff this instance still owns it (atomic via Lua)."""
        if self._is_lost():
            return False
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] "
            "then return redis.call('pexpire', KEYS[1], ARGV[2]) "
            "else return 0 end"
        )
        try:
            renewed = bool(
                self._client.eval(
                    script, 1, self._key, self._instance_id,
                    int(self._ttl_seconds * 1000),
                )
            )
        except redis.RedisError as exc:
            with self._lock:
                self._last_heartbeat_ok = False
            LEASE_HEARTBEATS_TOTAL.labels(outcome="redis_error").inc()
            logger.warning(
                f"Redis error renewing lease for {self.stream_id}: {exc}"
            )
            return False
        with self._lock:
            self._last_heartbeat_ok = renewed
        if renewed:
            LEASE_HEARTBEATS_TOTAL.labels(outcome="renewed").inc()
        else:
            self._mark_lost("renewal failed")
        return renewed

    def _is_lost(self) -> bool:
        with self._lock:
            return self._lost


class LeaderLease:
    """Redis-based single-active leader for periodic background work.

    ``claim()`` returns True exactly once per ``ttl_seconds`` across all
    replicas (``SET key instance NX EX ttl``). Followers see False and skip
    the cycle, so retention/expiry/flush passes run on one replica only. If
    the leader dies the key expires and another replica claims the next
    cycle. Redis failure returns True (fail-safe: work may duplicate, it is
    never skipped) because these passes are idempotent.
    """

    def __init__(self, client: "redis.Redis", key: str, instance_id: str, ttl_seconds: float) -> None:
        self._client = client
        self._key = f"omnitrack:leader:{key}"
        self._instance_id = instance_id
        self._ttl_seconds = ttl_seconds

    def claim(self) -> bool:
        try:
            return bool(
                self._client.set(
                    self._key, self._instance_id, nx=True, ex=int(self._ttl_seconds)
                )
            )
        except redis.RedisError as exc:
            logger.warning(
                f"Redis error claiming leader lease {self._key}: {exc}; "
                f"proceeding (work is idempotent)"
            )
            return True


class LeaseManager:
    """Per-process lease registry plus the shared heartbeat thread."""

    def __init__(
        self,
        client: "redis.Redis",
        *,
        instance_id: Optional[str] = None,
        ttl_seconds: float = 15.0,
        heartbeat_interval_seconds: float = 5.0,
        acquire_timeout_seconds: float = 5.0,
        on_lost: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._client = client
        self._instance_id = instance_id or _instance_identity()
        self._ttl_seconds = ttl_seconds
        self._heartbeat_interval = heartbeat_interval_seconds
        self._acquire_timeout = acquire_timeout_seconds
        self._on_lost = on_lost
        self._lock = threading.RLock()
        self._leases: dict[str, StreamLease] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def leases(self) -> dict[str, StreamLease]:
        with self._lock:
            return dict(self._leases)

    def owner_of(self, stream_id: str) -> Optional[str]:
        """Return the current Redis-recorded owner id of a stream, if any."""
        try:
            value = self._client.get(f"{LEASE_KEY_PREFIX}{stream_id}")
        except redis.RedisError as exc:
            logger.warning(f"Redis error reading owner for {stream_id}: {exc}")
            return None
        return value if value is not None else None

    def acquire(self, stream_id: str) -> Optional[StreamLease]:
        """Acquire the lease for ``stream_id``; ``None`` when not obtained."""
        with self._lock:
            existing = self._leases.get(stream_id)
            if existing is not None and not existing._is_lost():
                return existing
            lease = StreamLease(
                stream_id,
                self._client,
                self._instance_id,
                self._ttl_seconds,
                on_lost=self._on_lost,
            )
            if not lease.acquire(self._acquire_timeout):
                return None
            self._leases[stream_id] = lease
            self._ensure_thread_locked()
            return lease

    def release(self, stream_id: str) -> bool:
        """Release and forget the local lease for ``stream_id``."""
        with self._lock:
            lease = self._leases.pop(stream_id, None)
        if lease is None:
            return False
        return lease.release()

    def release_all(self) -> int:
        with self._lock:
            leases = list(self._leases.values())
            self._leases.clear()
        released = 0
        for lease in leases:
            if lease.release():
                released += 1
        return released

    def heartbeat_all(self) -> int:
        """Run one heartbeat round over every live lease (tests call directly)."""
        with self._lock:
            leases = list(self._leases.values())
        renewed = 0
        for lease in leases:
            if lease.heartbeat():
                renewed += 1
        LEASE_OWNED_STREAMS.set(
            sum(1 for lease in leases if not lease._is_lost())
        )
        return renewed

    def record_reassignment(self, stream_id: str) -> None:
        STREAM_REASSIGNMENTS_TOTAL.labels(stream_id=stream_id).inc()

    def start(self) -> None:
        with self._lock:
            self._ensure_thread_locked()

    def stop(self, grace_seconds: float = 5.0) -> int:
        """Stop the heartbeat thread and release every lease."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=grace_seconds)
        return self.release_all()

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._heartbeat_loop, name="lease-heartbeat", daemon=True
        )
        self._thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(self._heartbeat_interval):
            self.heartbeat_all()

    def stop_all(self, grace_seconds: float = 5.0) -> int:
        return self.stop(grace_seconds)
