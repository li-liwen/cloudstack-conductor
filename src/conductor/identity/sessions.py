"""Short-lived in-process registry for temporary legacy CloudStack sessions.

The multi-step first-login flow (verify -> MFA -> finish) may keep the native
CloudStack session until ``finish`` so the final step can log it out. Session
keys are bearer credentials, so the registry is memory-only:

* nothing is written to the database, audit log, or application log;
* entries expire after a short TTL and are closed on eviction;
* the registry is bounded, so one subject cannot exhaust memory.

If the deployment serves the flow from more than one worker, either use sticky
routing for the migration router or set
``CONDUCTOR_IDENTITY__CLOSE_LEGACY_SESSION_IMMEDIATELY=true`` so the session is
closed inside the verification request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from conductor.identity.protocols import LegacySession

logger = logging.getLogger(__name__)


@dataclass
class _Entry:
    session: LegacySession
    expires_at: float


class TemporarySessionRegistry:
    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        max_entries: int = 1024,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = max(1, int(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._monotonic = monotonic
        self._entries: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def put(self, subject: str, session: LegacySession) -> None:
        now = self._monotonic()
        close: list[LegacySession] = []
        async with self._lock:
            previous = self._entries.pop(subject, None)
            if previous is not None:
                close.append(previous.session)
            while len(self._entries) >= self._max_entries:
                oldest_subject = min(self._entries, key=lambda key: self._entries[key].expires_at)
                close.append(self._entries.pop(oldest_subject).session)
            expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
            for key in expired:
                close.append(self._entries.pop(key).session)
            self._entries[subject] = _Entry(session=session, expires_at=now + self._ttl)
        for item in close:
            await _close(item)

    async def peek(self, subject: str) -> LegacySession | None:
        now = self._monotonic()
        async with self._lock:
            entry = self._entries.get(subject)
        if entry is None:
            return None
        if entry.expires_at <= now:
            await self.pop(subject)
            return None
        return entry.session

    async def pop(self, subject: str) -> LegacySession | None:
        now = self._monotonic()
        async with self._lock:
            entry = self._entries.pop(subject, None)
        if entry is None:
            return None
        if entry.expires_at <= now:
            await _close(entry.session)
            return None
        return entry.session

    async def close_all(self) -> None:
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            await _close(entry.session)


async def _close(session: LegacySession) -> None:
    try:
        await session.logout()
    except Exception:
        logger.info("a temporary CloudStack session could not be logged out")
    try:
        await session.aclose()
    except Exception:
        logger.info("a temporary CloudStack session could not be closed")
