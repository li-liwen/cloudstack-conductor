"""OIDC authorization-code flow with PKCE.

The application never sees passwords. Only the ID token's verified claims are
used, and the stable ``sub`` claim is the identity key. Usernames and email
addresses are descriptive and are never used to merge identities.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from conductor.config import Settings
from conductor.errors import AuthenticationError, ConfigurationError
from conductor.security.sessions import OIDCFlow


@dataclass
class DiscoveryDocument:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None = None
    userinfo_endpoint: str | None = None
    id_token_signing_alg_values_supported: tuple[str, ...] = ("RS256",)


def same_issuer(left: object, right: object) -> bool:
    """Compare issuers while tolerating a trailing slash difference."""

    return str(left or "").rstrip("/") == str(right or "").rstrip("/")


class OIDCProvider:
    def __init__(self, settings: Settings) -> None:
        if not settings.auth.oidc_issuer:
            raise ConfigurationError("OIDC issuer is not configured")
        self.settings = settings
        self._issuer = settings.auth.oidc_issuer.rstrip("/")
        self._discovery: DiscoveryDocument | None = None
        self._discovered_at = 0.0
        self._jwks_client: jwt.PyJWKClient | None = None

    async def discovery(self) -> DiscoveryDocument:
        if self._discovery and time.monotonic() - self._discovered_at < 300:
            return self._discovery
        url = self._issuer + "/.well-known/openid-configuration"
        try:
            async with httpx.AsyncClient(timeout=self.settings.auth.oidc_discovery_timeout_seconds) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AuthenticationError("the identity provider is unavailable") from exc
        try:
            document = DiscoveryDocument(
                issuer=str(payload["issuer"]).rstrip("/"),
                authorization_endpoint=str(payload["authorization_endpoint"]),
                token_endpoint=str(payload["token_endpoint"]),
                jwks_uri=str(payload["jwks_uri"]),
                end_session_endpoint=payload.get("end_session_endpoint"),
                userinfo_endpoint=payload.get("userinfo_endpoint"),
                id_token_signing_alg_values_supported=tuple(
                    payload.get("id_token_signing_alg_values_supported") or ["RS256"]
                ),
            )
        except (KeyError, TypeError) as exc:
            raise AuthenticationError("the identity provider metadata is incomplete") from exc
        if document.issuer != self._issuer:
            raise AuthenticationError("the identity provider issuer does not match its configuration")
        self._discovery = document
        self._discovered_at = time.monotonic()
        self._jwks_client = jwt.PyJWKClient(document.jwks_uri, cache_keys=True, lifespan=300)
        return document

    async def authorization_url(self, redirect_uri: str, next_path: str = "/") -> tuple[str, OIDCFlow]:
        document = await self.discovery()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        )
        params = {
            "response_type": "code",
            "client_id": self.settings.auth.oidc_client_id or "",
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.settings.auth.oidc_scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        separator = "&" if "?" in document.authorization_endpoint else "?"
        url = document.authorization_endpoint + separator + urllib.parse.urlencode(params)
        flow = OIDCFlow(
            state=state,
            nonce=nonce,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            next_path=_safe_next(next_path),
        )
        return url, flow

    async def exchange(self, code: str, flow: OIDCFlow) -> dict[str, Any]:
        document = await self.discovery()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": flow.redirect_uri,
            "client_id": self.settings.auth.oidc_client_id or "",
            "client_secret": self.settings.oidc_client_secret(),
            "code_verifier": flow.code_verifier,
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    document.token_endpoint,
                    data=data,
                    headers={"Accept": "application/json"},
                )
                if response.status_code >= 400:
                    raise AuthenticationError("the identity provider rejected the login")
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AuthenticationError("the identity provider could not complete the login") from exc
        id_token = payload.get("id_token")
        if not isinstance(id_token, str):
            raise AuthenticationError("the identity provider returned no ID token")
        claims = await self.verify_id_token(id_token, document=document, nonce=flow.nonce)
        return claims

    async def verify_id_token(
        self, token: str, *, document: DiscoveryDocument | None = None, nonce: str | None = None
    ) -> dict[str, Any]:
        document = document or await self.discovery()
        if self._jwks_client is None:
            self._jwks_client = jwt.PyJWKClient(document.jwks_uri, cache_keys=True, lifespan=300)
        try:
            signing_key = await asyncio.to_thread(self._jwks_client.get_signing_key_from_jwt, token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("the identity token signature could not be checked") from exc
        algorithms = list(document.id_token_signing_alg_values_supported)
        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=algorithms,
                audience=self.settings.auth.oidc_client_id,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    "verify_aud": True,
                    # Compare the issuer below after normalizing a trailing
                    # slash; Authentik emits one in ``iss`` while discovery
                    # metadata and configuration may omit it.
                    "verify_iss": False,
                },
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("the identity token is not valid") from exc
        if not same_issuer(claims.get("iss"), document.issuer):
            raise AuthenticationError("the identity token issuer is not valid")
        if claims.get("azp") and claims["azp"] != self.settings.auth.oidc_client_id:
            raise AuthenticationError("the identity token was issued for another client")
        if nonce is not None and claims.get("nonce") != nonce:
            raise AuthenticationError("the identity token nonce does not match the login request")
        if not claims.get("sub"):
            raise AuthenticationError("the identity token has no subject")
        return claims

    async def logout_url(self, post_logout_redirect: str | None = None) -> str | None:
        document = await self.discovery()
        if not document.end_session_endpoint:
            return None
        params = {}
        if post_logout_redirect:
            params["post_logout_redirect_uri"] = post_logout_redirect
        return document.end_session_endpoint + ("?" + urllib.parse.urlencode(params) if params else "")


def mfa_satisfied(claims: dict[str, Any], claim_name: str = "acr") -> bool:
    amr = claims.get("amr") or []
    if isinstance(amr, str):
        amr = [amr]
    if any(str(item).lower() in {"mfa", "otp", "hwk", "swk", "fido"} for item in amr):
        return True
    acr = str(claims.get(claim_name) or "").lower()
    return any(marker in acr for marker in ("mfa", "loa2", "loa3", "urn:mace:incommon:iap:silver"))


def _safe_next(path: str) -> str:
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path[:500]
