"""Encrypted persistent store for MCP client registrations and tokens.

OIDCProxy stores client metadata, refresh tokens, and upstream token sets in an
AsyncKeyValue backend. This wrapper keeps that backend on a private volume and
encrypts every value with AES-GCM bound to its collection and key.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, SupportsFloat

from key_value.aio.protocols.key_value import AsyncKeyValue
from key_value.aio.stores.filetree import FileTreeStore

from conductor.security.crypto import Vault


class EncryptedFileStore(AsyncKeyValue):
    def __init__(self, directory: str | Path, vault: Vault) -> None:
        self._store = FileTreeStore(data_directory=Path(directory))
        self._vault = vault

    def _aad(self, key: str, collection: str | None) -> str:
        return f"mcp-storage:{collection or 'default'}:{key}"

    async def get(self, key: str, *, collection: str | None = None) -> dict[str, Any] | None:
        raw = await self._store.get(key, collection=collection)
        if raw is None:
            return None
        envelope = raw.get("__enc__")
        if not isinstance(envelope, str):
            return None
        try:
            payload = json.loads(self._vault.decrypt(envelope, aad=self._aad(key, collection)))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        envelope = self._vault.encrypt(serialized, aad=self._aad(key, collection))
        await self._store.put(key, {"__enc__": envelope}, collection=collection, ttl=ttl)

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        return await self._store.delete(key, collection=collection)

    async def ttl(self, key: str, *, collection: str | None = None) -> tuple[dict[str, Any] | None, float | None]:
        value = await self.get(key, collection=collection)
        _, remaining = await self._store.ttl(key, collection=collection)
        return value, remaining

    async def get_many(self, keys: Sequence[str], *, collection: str | None = None) -> list[dict[str, Any] | None]:
        return [await self.get(key, collection=collection) for key in keys]

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        for key, value in zip(keys, values, strict=True):
            await self.put(key, value, collection=collection, ttl=ttl)

    async def ttl_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[tuple[dict[str, Any] | None, float | None]]:
        return [await self.ttl(key, collection=collection) for key in keys]

    async def delete_many(self, keys: Sequence[str], *, collection: str | None = None) -> int:
        count = 0
        for key in keys:
            if await self.delete(key, collection=collection):
                count += 1
        return count
