"""Browser authentication routes: OIDC with PKCE, demo login, sessions."""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from conductor.api.deps import APIServices, services, session_data
from conductor.errors import AuthenticationError, ConfigurationError
from conductor.models.principal import SessionInfo
from conductor.security.sessions import (
    OIDCFlow,
    SessionData,
    clear_session_cookies,
    set_session_cookies,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
FLOW_COOKIE = "conductor_oidc_flow"


class DemoLoginRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=255)


def _redirect_uri(services_bundle: APIServices) -> str:
    return services_bundle.settings.public_base_url.rstrip("/") + services_bundle.settings.auth.oidc_redirect_path


@router.get("/login")
async def login(
    request: Request,
    next: str = Query(default="/", max_length=500),
    services_bundle: APIServices = Depends(services),
) -> Response:
    if services_bundle.settings.auth.mode == "demo":
        if not services_bundle.settings.auth.allow_demo_auth:
            raise AuthenticationError("login is not configured")
        return JSONResponse(
            status_code=400,
            content={
                "code": "demo_login_required",
                "message": "This demo deployment uses the demo login endpoint.",
            },
        )
    if services_bundle.oidc is None:
        raise ConfigurationError("the identity provider is not configured")
    url, flow = await services_bundle.oidc.authorization_url(_redirect_uri(services_bundle), next)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        FLOW_COOKIE,
        services_bundle.flow_codec.encode(flow),
        max_age=600,
        httponly=True,
        secure=services_bundle.settings.auth.secure_cookies,
        samesite="lax",
        path="/api/v1/auth",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    services_bundle: APIServices = Depends(services),
) -> Response:
    if services_bundle.settings.auth.mode != "oidc":
        raise AuthenticationError("the OIDC callback is not enabled")
    if not code or not state:
        raise AuthenticationError("the login callback is incomplete")
    flow_cookie = request.cookies.get(FLOW_COOKIE)
    if not flow_cookie:
        raise AuthenticationError("the login flow cookie is missing; start again")
    flow: OIDCFlow = services_bundle.flow_codec.decode(flow_cookie)
    if not secrets.compare_digest(flow.state, state):
        raise AuthenticationError("the login state does not match")
    if services_bundle.oidc is None:
        raise ConfigurationError("the identity provider is not configured")
    claims = await services_bundle.oidc.exchange(code, flow)
    session = services_bundle.session_factory()
    try:
        mapping_lookup = _MappingAdapter(session)
        principal = await services_bundle.principal_service.resolve_claims(claims, mapping_lookup)
    finally:
        await session.close()
    token, csrf = services_bundle.session_codec.encode(
        principal.subject,
        mfa=_mfa_from_claims(claims),
        amr=_amr_from_claims(claims),
    )
    response = RedirectResponse(flow.next_path or "/", status_code=303)
    response.delete_cookie(FLOW_COOKIE, path="/api/v1/auth")
    set_session_cookies(response, services_bundle.settings, token, csrf)
    return response


@router.post("/demo-login")
async def demo_login(
    request: Request,
    body: DemoLoginRequest,
    services_bundle: APIServices = Depends(services),
) -> Response:
    if not (
        services_bundle.settings.auth.mode == "demo"
        and services_bundle.settings.auth.allow_demo_auth
        and services_bundle.settings.environment != "production"
    ):
        raise AuthenticationError("demo login is not enabled")
    session = services_bundle.session_factory()
    try:
        principal = await services_bundle.principal_service.resolve_demo(body.subject, _MappingAdapter(session))
    finally:
        await session.close()
    token, csrf = services_bundle.session_codec.encode(principal.subject, mfa=False, amr=["pwd"])
    response = JSONResponse(
        status_code=200,
        content=SessionInfo(
            principal=principal,
            expires_at=_expiry(services_bundle),
            csrf_token=csrf,
            mfa=False,
        ).model_dump(mode="json"),
    )
    set_session_cookies(response, services_bundle.settings, token, csrf)
    return response


@router.get("/session")
async def get_session(
    request: Request,
    session: SessionData = Depends(session_data),
    services_bundle: APIServices = Depends(services),
) -> dict:
    from conductor.db.identity import IdentityRepository

    db = services_bundle.session_factory()
    try:
        mapping = await IdentityRepository(db).get_mapping(session.subject)
        if mapping is None:
            raise AuthenticationError("this identity is not provisioned")
        principal = services_bundle.principal_service.build_principal(mapping, amr=list(session.amr), mfa=session.mfa)
        return SessionInfo(
            principal=principal,
            expires_at=session.expires_at,
            csrf_token=session.csrf,
            mfa=session.mfa,
        ).model_dump(mode="json")
    finally:
        await db.close()


@router.post("/logout")
async def logout(
    request: Request,
    services_bundle: APIServices = Depends(services),
) -> Response:
    response = JSONResponse(status_code=200, content={"ok": True})
    clear_session_cookies(response, services_bundle.settings)
    if services_bundle.oidc is not None and services_bundle.settings.auth.mode == "oidc":
        try:
            url = await services_bundle.oidc.logout_url(services_bundle.settings.public_base_url.rstrip("/") + "/")
            if url:
                response = JSONResponse(status_code=200, content={"ok": True, "logout_url": url})
                clear_session_cookies(response, services_bundle.settings)
        except Exception:
            logger.info("identity provider logout URL unavailable")
    return response


class _MappingAdapter:
    def __init__(self, session) -> None:
        self.session = session

    async def get_mapping(self, subject: str):
        from conductor.db.identity import IdentityRepository

        return await IdentityRepository(self.session).get_mapping(subject)


def _expiry(services_bundle: APIServices):
    from datetime import timedelta

    from conductor.models.common import utcnow

    return utcnow() + timedelta(seconds=services_bundle.settings.auth.session_ttl_seconds)


def _amr_from_claims(claims: dict) -> list[str]:
    amr = claims.get("amr") or []
    return [str(item) for item in amr] if isinstance(amr, list) else [str(amr)]


def _mfa_from_claims(claims: dict) -> bool:
    from conductor.security.oidc import mfa_satisfied

    return mfa_satisfied(claims)
