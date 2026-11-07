"""Signed browser sessions and CSRF protection.

Sessions are stateless signed cookies. They contain only the identity subject,
a CSRF token, and timestamps. No credential, CloudStack token, or tenant data
is placed in the cookie.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.responses import Response

from conductor.config import Settings
from conductor.errors import AuthenticationError, AuthorizationError

SESSION_SALT = "conductor-session-v1"
OIDC_SALT = "conductor-oidc-flow-v1"


@dataclass(frozen=True)
class SessionData:
    subject: str
    csrf: str
    issued_at: datetime
    expires_at: datetime
    mfa: bool
    amr: tuple[str, ...]


class SessionCodec:
    def __init__(self, secret: str, *, ttl_seconds: int = 8 * 3600) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt=SESSION_SALT)
        self._ttl = ttl_seconds

    def encode(self, subject: str, *, mfa: bool = False, amr: list[str] | None = None) -> tuple[str, str]:
        csrf = secrets.token_urlsafe(32)
        now = datetime.now(tz=UTC)
        payload = {
            "sub": subject,
            "csrf": csrf,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self._ttl)).timestamp()),
            "mfa": mfa,
            "amr": list(amr or []),
        }
        return self._serializer.dumps(payload), csrf

    def decode(self, token: str) -> SessionData:
        try:
            payload: dict[str, Any] = self._serializer.loads(token, max_age=self._ttl)
        except SignatureExpired as exc:
            raise AuthenticationError("the session has expired") from exc
        except BadSignature as exc:
            raise AuthenticationError("the session is not valid") from exc
        expires = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
        if expires <= datetime.now(tz=UTC):
            raise AuthenticationError("the session has expired")
        return SessionData(
            subject=str(payload["sub"]),
            csrf=str(payload["csrf"]),
            issued_at=datetime.fromtimestamp(int(payload["iat"]), tz=UTC),
            expires_at=expires,
            mfa=bool(payload.get("mfa")),
            amr=tuple(payload.get("amr", [])),
        )


class CSRF:
    def __init__(self, header_name: str) -> None:
        self.header_name = header_name

    def validate(self, session: SessionData, cookie_token: str | None, header_token: str | None) -> None:
        if not cookie_token or not header_token:
            raise AuthorizationError("a CSRF token is required for this request")
        if not (
            secrets.compare_digest(cookie_token, header_token) and secrets.compare_digest(cookie_token, session.csrf)
        ):
            raise AuthorizationError("the CSRF token is not valid")


@dataclass
class OIDCFlow:
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    next_path: str


class OIDCFlowCodec:
    def __init__(self, secret: str) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt=OIDC_SALT)

    def encode(self, flow: OIDCFlow) -> str:
        return self._serializer.dumps(flow.__dict__)

    def decode(self, token: str) -> OIDCFlow:
        try:
            payload = self._serializer.loads(token, max_age=600)
        except (BadSignature, SignatureExpired) as exc:
            raise AuthenticationError("the login flow expired; start again") from exc
        return OIDCFlow(**payload)


def set_session_cookies(
    response: Response,
    settings: Settings,
    token: str,
    csrf: str,
    *,
    max_age: int | None = None,
) -> None:
    max_age = max_age or settings.auth.session_ttl_seconds
    secure = settings.auth.secure_cookies
    response.set_cookie(
        settings.auth.session_cookie_name,
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    # The CSRF cookie is readable by the portal JavaScript, but it is not an
    # authentication token: the signed session cookie must also be present.
    response.set_cookie(
        settings.auth.csrf_cookie_name,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.auth.session_cookie_name, path="/")
    response.delete_cookie(settings.auth.csrf_cookie_name, path="/")
