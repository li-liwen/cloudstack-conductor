"""Portal API application.

Serves the browser application and coordinates conversations, plans, approvals,
operations, estimates, documentation, and operator views. CloudStack credentials
never enter this process.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from conductor.agent.llm import ModelGateway
from conductor.api import auth, conversations, operations, operator, plans, resources, system
from conductor.api.deps import APIServices
from conductor.config import Settings
from conductor.db.session import build_engine, build_session_factory
from conductor.errors import AuthenticationError, ConductorError
from conductor.execution.client import ExecutionClient
from conductor.identity.config import IdentityBridgeSettings
from conductor.identity.routes import build_router as build_identity_router
from conductor.identity.wiring import build_identity_service
from conductor.logging import configure_logging, request_id_var
from conductor.security.oidc import OIDCProvider
from conductor.security.principal import PrincipalService
from conductor.security.rate_limit import InMemoryRateLimiter
from conductor.security.sessions import CSRF, OIDCFlowCodec, SessionCodec
from conductor.version import __version__
from conductor.worker.queue import build_agent_queue

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level, service="conductor-api")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.environment == "production":
            settings.validate_for_runtime()
        engine = build_engine(settings)
        session_factory = build_session_factory(engine)
        session_codec = SessionCodec(settings.session_secret(), ttl_seconds=settings.auth.session_ttl_seconds)
        flow_codec = OIDCFlowCodec(settings.session_secret())
        csrf = CSRF(settings.auth.csrf_header_name)
        oidc = OIDCProvider(settings) if settings.auth.mode == "oidc" else None
        execution = ExecutionClient(settings)
        model = ModelGateway(settings)
        agent_queue = build_agent_queue(settings, session_factory, execution, model)
        await agent_queue.open(start_worker=False)
        app.state.services = APIServices(
            settings=settings,
            session_factory=session_factory,
            execution=execution,
            agent_queue=agent_queue,
            session_codec=session_codec,
            flow_codec=flow_codec,
            csrf=csrf,
            oidc=oidc,
            rate_limiter=InMemoryRateLimiter(),
            principal_service=PrincipalService(settings),
        )
        app.state.engine = engine
        identity_service = None
        identity_settings = IdentityBridgeSettings()
        if identity_settings.enabled:
            identity_service = build_identity_service(identity_settings, session_factory)

            async def identity_subject(request: Request) -> str:
                token = request.cookies.get(settings.auth.session_cookie_name)
                if not token:
                    raise AuthenticationError("an authenticated identity is required")
                return session_codec.decode(token).subject

            async def identity_csrf(request: Request) -> None:
                token = request.cookies.get(settings.auth.session_cookie_name)
                if not token:
                    raise AuthenticationError("an authenticated identity is required")
                session = session_codec.decode(token)
                csrf.validate(
                    session,
                    request.cookies.get(settings.auth.csrf_cookie_name),
                    request.headers.get(settings.auth.csrf_header_name),
                )

            async def identity_rate(_key: str) -> bool:
                return True

            app.include_router(
                build_identity_router(
                    identity_service,
                    subject_resolver=identity_subject,
                    csrf_guard=identity_csrf,
                    rate_limiter=identity_rate,
                    require_tls=settings.public_base_url.startswith("https://"),
                )
            )
            app.state.identity = identity_service
        try:
            yield
        finally:
            if identity_service is not None:
                await identity_service.close()
            await agent_queue.close()
            await execution.close()
            await engine.dispose()

    app = FastAPI(
        title="CloudStack Conductor",
        version=__version__,
        docs_url="/api/docs" if settings.docs_enabled and settings.environment != "production" else None,
        openapi_url=("/api/openapi.json" if settings.docs_enabled and settings.environment != "production" else None),
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def _context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_var.set(request_id[:64])
        try:
            services_bundle: APIServices | None = getattr(app.state, "services", None)
            if (
                services_bundle is not None
                and request.url.path.startswith("/api/")
                and request.url.path not in {"/api/v1/config", "/api/v1/auth/login"}
            ):
                client = request.client.host if request.client else "unknown"
                allowed = await services_bundle.rate_limiter.allow(
                    f"api:{client}",
                    limit=services_bundle.settings.limits.api_rate_limit_per_minute,
                )
                if not allowed:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "code": "rate_limited",
                            "message": "Too many requests; retry later.",
                        },
                    )
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self' wss:; font-src 'self' data:; "
                "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            )
            if settings.public_base_url.startswith("https://"):
                response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
            return response
        finally:
            db = getattr(request.state, "db", None)
            if db is not None:
                await db.close()
            request_id_var.reset(token)

    if settings.allowed_origins and settings.environment != "production":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        )

    @app.exception_handler(ConductorError)
    async def _conductor_error(request: Request, exc: ConductorError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.public_message},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "invalid_request",
                "message": "The request was not valid.",
                "fields": sorted({".".join(str(part) for part in error.get("loc", [])) for error in exc.errors()})[:20],
            },
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled API error")
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "The request could not be completed."},
        )

    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(conversations.router)
    app.include_router(plans.router)
    app.include_router(operations.router)
    app.include_router(resources.router)
    app.include_router(operator.router)

    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    static_dir = Path(settings.portal_static_dir) if settings.portal_static_dir else None
    if static_dir and static_dir.is_dir():
        assets = static_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/branding/{filename}")
        async def branding_asset(filename: str):
            return _branding_file(settings, static_dir, filename)

        @app.get("/{path:path}")
        async def spa(path: str):
            if path.startswith(("api/", "healthz", "readyz", "metrics")):
                return JSONResponse(status_code=404, content={"code": "not_found"})
            candidate = static_dir / path
            if path and candidate.is_file() and static_dir in candidate.resolve().parents:
                return FileResponse(candidate)
            index = static_dir / "index.html"
            if index.is_file():
                return FileResponse(index, headers={"Cache-Control": "no-store"})
            return JSONResponse(
                status_code=503,
                content={
                    "code": "portal_unavailable",
                    "message": "The portal bundle is not built.",
                },
            )

        return

    @app.get("/")
    async def root() -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content={
                "service": "cloudstack-conductor",
                "version": __version__,
                "message": "API is running; the portal bundle is not installed in this environment.",
            },
        )


def _branding_file(settings: Settings, static_dir: Path, filename: str):
    import re

    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", filename):
        return JSONResponse(status_code=404, content={"code": "not_found"})
    candidates = []
    if settings.branding_assets_dir:
        candidates.append(Path(settings.branding_assets_dir) / filename)
    candidates.append(static_dir / "branding" / filename)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        allowed_root = (
            Path(settings.branding_assets_dir).resolve()
            if settings.branding_assets_dir
            else (static_dir / "branding").resolve()
        )
        if resolved.is_file() and allowed_root in resolved.parents:
            return FileResponse(resolved, headers={"Cache-Control": "public, max-age=300"})
    return JSONResponse(status_code=404, content={"code": "not_found"})
