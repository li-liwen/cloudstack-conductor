"""Small in-process rate limiter.

The reverse proxy also enforces limits. This limiter protects against a
single authenticated subject or client overwhelming one API process; in a
multi-worker deployment each worker has its own window, so the effective
limit is per worker.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class _Window:
    started_at: float
    count: int


@dataclass
class InMemoryRateLimiter:
    max_keys: int = 20_000
    _windows: dict[str, _Window] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def allow(self, key: str, *, limit: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        async with self._lock:
            window = self._windows.get(key)
            if window is None or now - window.started_at >= window_seconds:
                self._windows[key] = _Window(started_at=now, count=1)
                if len(self._windows) > self.max_keys:
                    self._prune(now, window_seconds)
                return True
            window.count += 1
            return window.count <= limit

    def _prune(self, now: float, window_seconds: int) -> None:
        stale = [key for key, window in self._windows.items() if now - window.started_at >= window_seconds]
        for key in stale[: max(1, len(stale))]:
            self._windows.pop(key, None)
