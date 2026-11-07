"""Minimal authentik admin API adapter.

Only the operations needed by the migration flow are implemented:

* locate a user by the stable subject (authentik user UUID, UID, or numeric pk);
* set a user's password through the supported ``set_password`` action;
* read the user's confirmed authenticator devices;
* revoke a user's authenticated sessions.

The bearer token is read from a mounted secret file and is never logged or
included in an error. Usernames and email addresses are never used to resolve
an identity.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any
from urllib.parse import quote

import httpx

from conductor.errors import ConfigurationError
from conductor.identity.errors import (
    IdentityNotProvisioned,
    IdentityProviderCapabilityError,
    IdentityProviderError,
)
from conductor.identity.models import AuthentikUser

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _is_uuid(value: str) -> bool:
    if not _UUID_RE.match(value):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _results(payload: object) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get("results")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _user(payload: dict[str, Any]) -> AuthentikUser | None:
    try:
        pk = int(payload["pk"])
    except (KeyError, TypeError, ValueError):
        return None
    groups: list[str] = []
    groups_obj = payload.get("groups_obj")
    if isinstance(groups_obj, list):
        for group in groups_obj:
            if isinstance(group, dict) and isinstance(group.get("name"), str):
                groups.append(group["name"])
    attributes = payload.get("attributes")
    return AuthentikUser(
        pk=pk,
        uuid=str(payload.get("uuid") or ""),
        uid=str(payload.get("uid") or ""),
        username=str(payload.get("username") or ""),
        name=str(payload.get("name") or ""),
        is_active=bool(payload.get("is_active", True)),
        groups=tuple(groups),
        attributes=attributes if isinstance(attributes, dict) else {},
    )


class AuthentikAdminAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        api_prefix: str,
        token: str,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token:
            raise ConfigurationError("the authentik service token is empty")
        if not base_url.startswith(("https://", "http://")):
            raise ConfigurationError("the authentik base URL must be HTTP(S)")
        base = base_url.rstrip("/")
        prefix = api_prefix.rstrip("/")
        self._base_url = base if base.endswith(prefix) else base + prefix
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds)),
            transport=transport,
            follow_redirects=False,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "cloudstack-conductor-identity/0.1",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def find_user_by_subject(self, subject: str) -> AuthentikUser | None:
        value = subject.strip()
        if not value or len(value) > 255:
            return None
        candidates: list[dict[str, Any]] = []
        if _is_uuid(value):
            response = await self._get("/core/users/", params={"uuid": value, "page_size": 50})
            if response.status_code == 400:
                response = await self._get("/core/users/", params={"search": value, "page_size": 50})
            self._ensure_status(response, expected={200})
            candidates = _results(self._json(response))
        else:
            response = await self._get("/core/users/", params={"search": value, "page_size": 50})
            self._ensure_status(response, expected={200})
            candidates = _results(self._json(response))

        matches: list[AuthentikUser] = []
        for item in candidates:
            parsed = _user(item)
            if parsed is None:
                continue
            if parsed.uuid == value or parsed.uid == value or str(parsed.pk) == value:
                matches.append(parsed)
        if len(matches) > 1:
            raise IdentityProviderError("the authentik subject matched more than one user")
        return matches[0] if matches else None

    async def set_password(self, subject: str, password: str) -> None:
        if not password:
            raise IdentityProviderError("the password could not be applied")
        user = await self._require_user(subject)
        path = f"/core/users/{user.pk}/set_password/"
        try:
            response = await self._client.post(self._base_url + path, json={"password": password})
        except httpx.HTTPError as exc:
            raise IdentityProviderError("authentik could not be reached") from exc
        if response.status_code != 204:
            logger.warning("authentik set_password failed: status=%s", response.status_code)
            raise IdentityProviderError("authentik did not accept the password update")

    async def mfa_ready(self, subject: str) -> bool:
        user = await self._require_user(subject)
        response = await self._get("/authenticators/admin/all/", params={"user": user.pk})
        if response.status_code != 404:
            self._ensure_status(response, expected={200})
            return self._confirmed(_results(self._json(response)))
        # Older authentik releases do not expose the union endpoint. Query the
        # per-type admin collections and filter by the user's numeric pk.
        checked = False
        for kind in ("totp", "webauthn", "static", "duo", "email", "sms", "endpoint"):
            response = await self._get(f"/authenticators/admin/{kind}/", params={"page_size": 200})
            if response.status_code == 404:
                continue
            self._ensure_status(response, expected={200})
            checked = True
            devices = _results(self._json(response))
            for device in devices:
                owner = device.get("user")
                if isinstance(owner, dict) and owner.get("pk") is not None:
                    try:
                        if int(owner["pk"]) != user.pk:
                            continue
                    except (TypeError, ValueError):
                        continue
                if bool(device.get("confirmed")):
                    return True
        if not checked:
            raise IdentityProviderCapabilityError(
                "authentik does not expose an authenticator-device API for this deployment"
            )
        return False

    async def revoke_sessions(self, subject: str) -> int:
        user = await self._require_user(subject)
        response = await self._get("/core/authenticated_sessions/", params={"user": user.pk, "page_size": 100})
        self._ensure_status(response, expected={200})
        revoked = 0
        for session in _results(self._json(response)):
            session_uuid = session.get("uuid")
            if not isinstance(session_uuid, str) or not session_uuid:
                continue
            path = f"/core/authenticated_sessions/{quote(session_uuid, safe='')}/"
            try:
                delete = await self._client.delete(self._base_url + path)
            except httpx.HTTPError as exc:
                raise IdentityProviderError("authentik could not be reached") from exc
            if delete.status_code in {204, 404}:
                if delete.status_code == 204:
                    revoked += 1
                continue
            logger.warning("authentik session revoke failed: status=%s", delete.status_code)
            raise IdentityProviderError("authentik did not revoke a session")
        return revoked

    async def _require_user(self, subject: str) -> AuthentikUser:
        user = await self.find_user_by_subject(subject)
        if user is None or not user.is_active:
            raise IdentityNotProvisioned()
        return user

    @staticmethod
    def _confirmed(devices: list[dict[str, Any]]) -> bool:
        # Only an explicitly confirmed device counts. A pending enrollment is
        # not MFA readiness.
        return any(bool(device.get("confirmed")) for device in devices)

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        try:
            return await self._client.get(self._base_url + path, params=params)
        except httpx.HTTPError as exc:
            raise IdentityProviderError("authentik could not be reached") from exc

    @staticmethod
    def _json(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError as exc:
            raise IdentityProviderError("authentik returned an unexpected response") from exc

    @staticmethod
    def _ensure_status(response: httpx.Response, *, expected: set[int]) -> None:
        if response.status_code in expected:
            return
        logger.warning("authentik request failed: status=%s", response.status_code)
        raise IdentityProviderError("authentik rejected the request")
