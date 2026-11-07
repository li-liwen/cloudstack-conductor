"""API dependencies and shared service bundle."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.config import Settings
from conductor.db.identity import IdentityRepository
from conductor.errors import AuthenticationError, AuthorizationError
from conductor.execution.client import ExecutionClient
from conductor.models.enums import MappingStage, PrincipalRole
from conductor.models.principal import Principal
from conductor.security.oidc import OIDCProvider
from conductor.security.principal import PrincipalService
from conductor.security.rate_limit import InMemoryRateLimiter
from conductor.security.sessions import CSRF, OIDCFlowCodec, SessionCodec, SessionData
from conductor.worker.queue import AgentQueue


@dataclass
class APIServices:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    execution: ExecutionClient
    agent_queue: AgentQueue
    session_codec: SessionCodec
    flow_codec: OIDCFlowCodec
    csrf: CSRF
    oidc: OIDCProvider | None
    rate_limiter: InMemoryRateLimiter
    principal_service: PrincipalService


@dataclass(frozen=True)
class RequestContext:
    principal: Principal
    session: SessionData


def services(request: Request) -> APIServices:
    return request.app.state.services


def client_ip(request: Request, settings: Settings) -> str:
    """Resolve the client address, trusting forwarding headers only from proxies."""

    peer = request.client.host if request.client else "unknown"
    trusted = set(settings.trusted_proxy_cidrs)
    if trusted and peer in trusted:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
    return peer[:64]


def session_data(request: Request, services: APIServices = Depends(services)) -> SessionData:
    token = request.cookies.get(services.settings.auth.session_cookie_name)
    if not token:
        raise AuthenticationError()
    return services.session_codec.decode(token)


async def _load_principal(subject: str, services: APIServices) -> tuple[Principal, AsyncSession]:
    session = services.session_factory()
    try:
        mapping = await IdentityRepository(session).get_mapping(subject)
        if mapping is None:
            raise AuthenticationError("this identity is not provisioned")
        if mapping.stage is not MappingStage.COMPLETE:
            raise AuthorizationError("identity migration is not complete")
        principal = services.principal_service.build_principal(mapping)
        return principal, session
    except Exception:
        await session.close()
        raise


async def current_context(
    request: Request,
    session: SessionData = Depends(session_data),
    services: APIServices = Depends(services),
) -> RequestContext:
    principal, db = await _load_principal(session.subject, services)
    request.state.db = db
    request.state.context = RequestContext(principal=principal, session=session)
    return RequestContext(principal=principal, session=session)


async def current_principal(context: RequestContext = Depends(current_context)) -> Principal:
    return context.principal


async def require_csrf(
    request: Request,
    context: RequestContext = Depends(current_context),
    services: APIServices = Depends(services),
) -> RequestContext:
    cookie = request.cookies.get(services.settings.auth.csrf_cookie_name)
    header = request.headers.get(services.settings.auth.csrf_header_name)
    services.csrf.validate(context.session, cookie, header)
    return context


async def require_operator_read(
    context: RequestContext = Depends(current_context),
) -> Principal:
    if PrincipalRole.OPERATOR not in context.principal.roles:
        raise AuthorizationError("operator access is required")
    return context.principal


async def require_operator(context: RequestContext = Depends(require_csrf)) -> Principal:
    if PrincipalRole.OPERATOR not in context.principal.roles:
        raise AuthorizationError("operator access is required")
    return context.principal


async def db_session(request: Request) -> AsyncSession:
    session = getattr(request.state, "db", None)
    if session is None:
        services_bundle = services(request)
        session = services_bundle.session_factory()
        request.state.db = session
    return session


async def close_request_session(request: Request) -> None:
    session = getattr(request.state, "db", None)
    if session is not None:
        await session.close()
