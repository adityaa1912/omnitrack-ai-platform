from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, Set, Tuple


class AlertBroadcaster:
    def __init__(self, max_queue: int = 100) -> None:
        self._lock = threading.Lock()
        self._subscribers: Set[Tuple[Any, Any]] = set()
        self._max_queue = max_queue

    @property
    def max_queue(self) -> int:
        return self._max_queue

    def register(self, loop, queue) -> None:
        with self._lock:
            self._subscribers.add((loop, queue))

    def unregister(self, loop, queue) -> None:
        with self._lock:
            self._subscribers.discard((loop, queue))

    def publish(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._enqueue, queue, payload)
            except RuntimeError:
                pass

    @staticmethod
    def _enqueue(queue, payload) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass
