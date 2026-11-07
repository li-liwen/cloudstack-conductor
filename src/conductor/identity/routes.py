"""FastAPI router for the identity-migration flow.

Mounting is intentionally explicit because the portal API may not include these
routes yet, and because the migration endpoints authenticate a subject whose
mapping is not complete. The existing ``current_principal`` dependency rejects
that case, so the integration owner supplies a subject resolver based on the
portal session cookie:

.. code-block:: python

    from conductor.api.deps import services, session_data
    from conductor.identity.routes import build_router

    def subject_from_session(request, bundle=Depends(services), session=Depends(session_data)):
        return session.subject

    app.include_router(
        build_router(
            identity_service,
            subject_resolver=subject_from_session,
            csrf_guard=portal_csrf_guard,
            rate_limiter=portal_rate_limit,
        )
    )

The router also works without a custom CSRF hook: it then requires JSON
requests and enforces a same-origin ``Origin``/``Sec-Fetch-Site`` check. That is
a defence in depth for cookie-authenticated browsers, not a replacement for a
per-session CSRF token. Supply the portal's existing CSRF dependency for a full
token check.

Rate limiting is a hook rather than a hard-coded policy so the portal can use
its existing limiter. If no hook is supplied, the router relies on the reverse
proxy and the application-wide limiter.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from conductor.errors import AuthenticationError, AuthorizationError, RateLimitExceeded, ValidationFailure
from conductor.identity.errors import TlsRequired
from conductor.identity.models import MigrationStatus
from conductor.identity.service import IdentityMigrationService
from conductor.security.rate_limit import InMemoryRateLimiter

SubjectResolver = Callable[[Request], str | Awaitable[str]]
CSRFGuard = Callable[[Request], Awaitable[None]]
RateLimiterHook = Callable[[str], Awaitable[bool]]

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class LegacyVerifyRequest(BaseModel):
    """The pending legacy password is accepted only in the request body."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=255)
    password: SecretStr = Field(min_length=1, max_length=1024)
    otp: SecretStr | None = Field(default=None, max_length=64)


class SameOriginCSRFGuard:
    """Fallback CSRF guard for JSON, cookie-authenticated POST requests."""

    async def __call__(self, request: Request) -> None:
        if request.method.upper() in _SAFE_METHODS:
            return
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        length = request.headers.get("content-length")
        has_body = (length is not None and length != "0") or request.headers.get("transfer-encoding") is not None
        if has_body and content_type != "application/json":
            raise ValidationFailure("identity migration requests must use application/json")
        origin = request.headers.get("origin")
        if origin is None:
            fetch_site = request.headers.get("sec-fetch-site")
            if fetch_site is not None and fetch_site not in {"same-origin", "none"}:
                raise AuthorizationError("cross-origin identity migration request rejected")
            return
        expected = f"{request.url.scheme}://{request.url.netloc}".rstrip("/")
        if origin.rstrip("/") != expected:
            raise AuthorizationError("cross-origin identity migration request rejected")


async def _resolve_subject(request: Request, resolver: SubjectResolver) -> str:
    result = resolver(request)
    if inspect.isawaitable(result):
        result = await result
    subject = str(result or "")
    if not subject:
        raise AuthenticationError("an authenticated identity is required")
    return subject


def build_router(
    service: IdentityMigrationService,
    *,
    subject_resolver: SubjectResolver | None = None,
    csrf_guard: CSRFGuard | None = None,
    rate_limiter: RateLimiterHook | None = None,
    rate_limit_per_minute: int = 10,
    require_tls: bool | None = None,
    trust_forwarded_proto: bool = False,
    prefix: str = "/api/v1/identity",
) -> APIRouter:
    if rate_limit_per_minute < 1:
        raise ValueError("rate_limit_per_minute must be positive")
    tls_required = service.require_tls if require_tls is None else require_tls
    guard: CSRFGuard = csrf_guard or SameOriginCSRFGuard()
    local_limiter = InMemoryRateLimiter()

    async def default_subject(request: Request) -> str:
        principal = getattr(request.state, "principal", None)
        if principal is None:
            context = getattr(request.state, "context", None)
            principal = getattr(context, "principal", None)
        subject = getattr(principal, "subject", None)
        if not subject:
            raise AuthenticationError("an authenticated identity is required")
        return str(subject)

    resolver = subject_resolver or default_subject

    async def subject(request: Request) -> str:
        return await _resolve_subject(request, resolver)

    async def tls(request: Request) -> None:
        if not tls_required:
            return
        scheme = request.url.scheme
        if trust_forwarded_proto:
            forwarded = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
            if forwarded:
                scheme = forwarded
        if scheme != "https":
            raise TlsRequired()

    async def csrf(request: Request) -> None:
        await guard(request)

    async def rate_limit(authenticated_subject: str = Depends(subject)) -> None:
        key = f"identity:{authenticated_subject}"
        if not await local_limiter.allow(key, limit=rate_limit_per_minute):
            raise RateLimitExceeded()
        if rate_limiter is not None and not await rate_limiter(key):
            raise RateLimitExceeded()

    async def current_subject(authenticated_subject: str = Depends(subject)) -> str:
        return authenticated_subject

    router = APIRouter(
        prefix=prefix,
        tags=["identity"],
        dependencies=[Depends(tls), Depends(rate_limit)],
    )

    @router.get("/migration", response_model=MigrationStatus)
    async def get_migration(authenticated_subject: str = Depends(current_subject)) -> MigrationStatus:
        """Return the current migration stage for the authenticated subject."""

        return await service.status(authenticated_subject)

    @router.post(
        "/migration/legacy-verify",
        response_model=MigrationStatus,
        dependencies=[Depends(csrf)],
    )
    async def legacy_verify(
        body: LegacyVerifyRequest,
        authenticated_subject: str = Depends(current_subject),
    ) -> MigrationStatus:
        """Verify the legacy CloudStack password and move it into authentik."""

        password = body.password.get_secret_value()
        otp = body.otp.get_secret_value() if body.otp is not None else None
        try:
            return await service.legacy_verify(
                authenticated_subject,
                username=body.username,
                password=password,
                otp=otp,
                transport_secure=True,
            )
        finally:
            password = ""
            otp = None

    @router.post(
        "/migration/complete-mfa",
        response_model=MigrationStatus,
        dependencies=[Depends(csrf)],
    )
    async def complete_mfa(authenticated_subject: str = Depends(current_subject)) -> MigrationStatus:
        """Require/confirm authentik MFA before CloudStack SAML authorization."""

        return await service.complete_mfa(authenticated_subject)

    @router.post(
        "/migration/finish",
        response_model=MigrationStatus,
        dependencies=[Depends(csrf)],
    )
    async def finish(authenticated_subject: str = Depends(current_subject)) -> MigrationStatus:
        """Authorize the domain SAML provider, close the legacy session, and complete."""

        return await service.finish(authenticated_subject)

    @router.post(
        "/migration/abort",
        response_model=MigrationStatus,
        dependencies=[Depends(csrf)],
    )
    async def abort(authenticated_subject: str = Depends(current_subject)) -> MigrationStatus:
        """Close any pending legacy session without moving the stage backwards."""

        return await service.abort(authenticated_subject)

    return router


def rate_limiter_from_object(limiter: Any, *, limit: int) -> RateLimiterHook:
    """Adapt the portal's ``InMemoryRateLimiter``-style object to the hook.

    The adapter keeps the router independent of the portal's service bundle.
    """

    async def allow(key: str) -> bool:
        result = limiter.allow(key, limit=limit)
        if inspect.isawaitable(result):
            return bool(await result)
        return bool(result)

    return allow
