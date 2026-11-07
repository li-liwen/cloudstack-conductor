"""Native CloudStack username/password login for credential verification.

CloudStack 4.22 requires the ``login`` command to use HTTP POST, which is also
what we want here: the password and one-time code travel in a form body, never
in a URL. The temporary session is authenticated with CloudStack's own session
cookie and ``sessionkey``; no API key or admin credential is involved.

CloudStack's JSON login response historically exposes the user UUID and domain
UUID but not the account UUID. After a successful login we call ``listUsers``
with the temporary session (a normal user may list their own user) to obtain
``accountid`` so the caller can compare it with the pre-provisioned mapping.
If a CloudStack version cannot return that value, the adapter fails with a
clear capability error instead of guessing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from conductor.cloudstack.errors import CloudStackProtocolError
from conductor.errors import CloudStackError, CloudStackUnavailable, ConfigurationError
from conductor.identity.errors import (
    IdentityProviderCapabilityError,
    LegacyCredentialsInvalid,
    LegacySecondFactorInvalid,
    LegacySecondFactorRequired,
)

logger = logging.getLogger(__name__)


def _body(payload: dict[str, Any], command: str) -> dict[str, Any]:
    key = command.lower() + "response"
    body = payload.get(key)
    if isinstance(body, dict):
        return body
    for value in payload.values():
        if isinstance(value, dict):
            return value
    return payload


def _error(body: dict[str, Any]) -> tuple[str | None, str | None]:
    for value in body.values():
        if isinstance(value, dict) and ("errorcode" in value or "errortext" in value):
            return (
                str(value["errorcode"]) if value.get("errorcode") is not None else None,
                str(value.get("errortext") or "")[:300],
            )
    if "errorcode" in body:
        return (
            str(body["errorcode"]) if body.get("errorcode") is not None else None,
            str(body.get("errortext") or "")[:300],
        )
    return None, None


def _flag(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


@dataclass
class CloudStackLoginSession:
    """An in-memory temporary CloudStack session.

    ``session_key`` and the HTTP client's cookies are bearer material. They are
    excluded from the representation and must never be persisted or logged.
    """

    user_id: str
    account_id: str
    domain_id: str
    session_key: str = field(repr=False)
    _client: httpx.AsyncClient = field(repr=False, compare=False)
    _adapter: CloudStackNativeLoginAdapter = field(repr=False, compare=False)
    mfa_enabled: bool = False
    mfa_verified: bool = False
    provider: str | None = None

    async def logout(self) -> None:
        await self._adapter.logout(self._client, self.session_key)

    async def aclose(self) -> None:
        await self._client.aclose()


class CloudStackNativeLoginAdapter:
    """Customer login adapter. It never reads a provisioner API key."""

    def __init__(
        self,
        *,
        api_url: str,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_url.startswith(("https://", "http://")):
            raise ConfigurationError("the CloudStack API URL must be HTTP(S)")
        self._api_url = api_url
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds))
        self._transport = transport

    async def native_login(
        self,
        username: str,
        password: str,
        domain_id: str,
        otp: str | None = None,
    ) -> CloudStackLoginSession:
        if not username or not password or not domain_id:
            raise LegacyCredentialsInvalid("the legacy credentials are incomplete")
        if otp is not None and (not otp or len(otp) > 64):
            raise LegacySecondFactorInvalid("the legacy second-factor code is invalid")

        client = httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            follow_redirects=False,
            headers={
                "Accept": "application/json",
                "User-Agent": "cloudstack-conductor-identity/0.1",
            },
        )
        session_key: str | None = None
        try:
            login = _body(
                await self._post(
                    client,
                    {
                        "command": "login",
                        "response": "json",
                        "username": username,
                        "password": password,
                        "domainid": domain_id,
                    },
                ),
                "login",
            )
            code, _ = _error(login)
            if code is not None:
                logger.warning("CloudStack legacy login failed: code=%s", code)
                raise LegacyCredentialsInvalid()
            user_id = str(_first(login, "userid", "userid_uuid") or "")
            response_domain_id = str(_first(login, "domainid", "domainid_uuid") or "")
            session_key = str(_first(login, "sessionkey") or "")
            if not user_id or not response_domain_id or not session_key:
                raise IdentityProviderCapabilityError(
                    "the CloudStack login response did not contain a user, domain, and session key"
                )

            enabled_flag = _flag(_first(login, "is2faenabled", "is2FAenabled"))
            verified_flag = _flag(_first(login, "is2faverified", "is2FAverified"))
            if enabled_flag is None or verified_flag is None:
                # Do not silently treat an unknown 2FA state as "disabled".
                raise IdentityProviderCapabilityError(
                    "the CloudStack login response did not report its two-factor state"
                )
            provider = _first(login, "providerfor2fa", "providerFor2FA")
            mfa_enabled = enabled_flag
            mfa_verified = verified_flag

            account_id = str(_first(login, "accountid", "accountid_uuid") or "")
            if not account_id:
                account_id = await self._account_id(client, user_id, session_key)

            if mfa_enabled:
                if not otp:
                    raise LegacySecondFactorRequired()
                if not mfa_verified:
                    await self._validate_otp(client, otp, session_key)
                    mfa_verified = True

            return CloudStackLoginSession(
                user_id=user_id,
                account_id=account_id,
                domain_id=response_domain_id,
                session_key=session_key,
                mfa_enabled=mfa_enabled,
                mfa_verified=mfa_verified,
                provider=str(provider) if provider else None,
                _client=client,
                _adapter=self,
            )
        except Exception:
            if session_key:
                await self._logout_quietly(client, session_key)
            await client.aclose()
            raise

    async def _account_id(self, client: httpx.AsyncClient, user_id: str, session_key: str) -> str:
        try:
            users = _body(
                await self._post(
                    client,
                    {
                        "command": "listUsers",
                        "response": "json",
                        "id": user_id,
                        "sessionkey": session_key,
                    },
                ),
                "listUsers",
            )
        except CloudStackError as exc:
            raise IdentityProviderCapabilityError(
                "the CloudStack account identifier could not be read with the temporary session"
            ) from exc
        code, _ = _error(users)
        if code is not None:
            raise IdentityProviderCapabilityError(
                "the CloudStack account identifier could not be read with the temporary session"
            )
        items = users.get("user") or []
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise IdentityProviderCapabilityError("the CloudStack user response was incomplete")
        account_id = str(items[0].get("accountid") or "")
        if not account_id:
            raise IdentityProviderCapabilityError("the CloudStack user response had no account identifier")
        return account_id

    async def _validate_otp(self, client: httpx.AsyncClient, otp: str, session_key: str) -> None:
        try:
            body = _body(
                await self._post(
                    client,
                    {
                        "command": "validateUserTwoFactorAuthenticationCode",
                        "response": "json",
                        "codefor2fa": otp,
                        "sessionkey": session_key,
                    },
                ),
                "validateUserTwoFactorAuthenticationCode",
            )
        except CloudStackError as exc:
            raise LegacySecondFactorInvalid() from exc
        code, _ = _error(body)
        if code is not None:
            logger.warning("CloudStack legacy second-factor validation failed: code=%s", code)
            raise LegacySecondFactorInvalid()

    async def logout(self, client: httpx.AsyncClient, session_key: str) -> None:
        try:
            await self._post(
                client,
                {"command": "logout", "response": "json", "sessionkey": session_key},
            )
        except CloudStackError:
            logger.info("temporary CloudStack session logout did not complete")

    async def _logout_quietly(self, client: httpx.AsyncClient, session_key: str) -> None:
        try:
            await self.logout(client, session_key)
        except Exception:
            logger.info("temporary CloudStack session could not be closed")

    async def _post(self, client: httpx.AsyncClient, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = await client.post(self._api_url, data=data)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise CloudStackUnavailable("the CloudStack API could not be reached") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise CloudStackProtocolError("the CloudStack API returned non-JSON content") from exc
        if not isinstance(payload, dict):
            raise CloudStackProtocolError("the CloudStack API returned an unexpected payload")
        return payload
