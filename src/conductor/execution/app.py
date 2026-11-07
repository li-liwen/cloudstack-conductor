"""Internal execution API.

Only the portal API, agent worker, and MCP service may call this service, using
the shared internal token over a private Compose network. Every request carries
an authenticated principal subject; the execution service reconstructs the
mapped principal from the database and never trusts a caller-supplied role.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import Response

from conductor.config import Settings
from conductor.db.session import build_engine, build_session_factory, database_ready
from conductor.errors import (
    AuthenticationError,
    ConductorError,
    ConfigurationError,
)
from conductor.execution.queue import build_operation_queue
from conductor.execution.service import ExecutionService
from conductor.logging import configure_logging, request_id_var
from conductor.security.crypto import Vault, constant_time_equals
from conductor.version import __version__

logger = logging.getLogger(__name__)

REQUESTS = Counter("conductor_execution_requests_total", "Internal execution requests", ["route", "status"])
LATENCY = Histogram("conductor_execution_request_seconds", "Internal execution latency", ["route"])


class PreparePlanRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    action: str = Field(min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)
    request_id: str | None = Field(default=None, max_length=64)


class ApproveRequest(BaseModel):
    subject: str
    digest: str = Field(min_length=64, max_length=64)
    note: str | None = Field(default=None, max_length=500)
    client_fingerprint: str | None = Field(default=None, max_length=64)
    request_id: str | None = None


class RejectRequest(BaseModel):
    subject: str
    request_id: str | None = None


class QuoteRequest(BaseModel):
    subject: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ConsoleLeaseRequest(BaseModel):
    subject: str
    vm_id: str


class SubjectRequest(BaseModel):
    subject: str


class ConsoleExchangeRequest(BaseModel):
    subject: str
    lease_id: str
    token: str


def create_app(settings: Settings | None = None, *, start_worker: bool = True) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level, service="conductor-execution")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.validate_for_execution()
        if settings.environment == "production":
            settings.validate_for_runtime()
        engine = build_engine(settings)
        session_factory = build_session_factory(engine)
        vault = Vault(settings.vault_key())
        queue = build_operation_queue(settings, session_factory, vault)
        await queue.open(start_worker=start_worker, concurrency=2)
        service = ExecutionService(settings, session_factory, vault, queue)
        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.vault = vault
        app.state.queue = queue
        app.state.execution = service
        recovered = await service.recover_queued()
        if recovered:
            logger.info("re-enqueued %s queued operations", recovered)
        try:
            yield
        finally:
            await queue.close()
            await engine.dispose()

    app = FastAPI(
        title="CloudStack Conductor Execution Service",
        version=__version__,
        docs_url="/internal/docs" if settings.docs_enabled else None,
        openapi_url="/internal/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_var.set(request_id[:64])
        route = request.url.path
        try:
            with LATENCY.labels(route=route).time():
                response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            REQUESTS.labels(route=route, status=str(response.status_code)).inc()
            return response
        finally:
            request_id_var.reset(token)

    @app.exception_handler(ConductorError)
    async def _conductor_error(request: Request, exc: ConductorError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.public_message},
        )

    @app.exception_handler(ConfigurationError)
    async def _configuration_error(request: Request, exc: ConfigurationError) -> JSONResponse:
        logger.error("execution configuration problem: %s", exc.detail)
        return JSONResponse(
            status_code=500,
            content={"code": "configuration_error", "message": "The service is not configured."},
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled execution error")
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "The request could not be completed."},
        )

    async def require_internal(
        request: Request,
        x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
    ) -> None:
        expected = request.app.state.settings.internal_token()
        if not x_internal_token or not constant_time_equals(x_internal_token, expected):
            raise AuthenticationError("internal service authentication failed")

    internal = Depends(require_internal)

    def execution(request: Request) -> ExecutionService:
        return request.app.state.execution

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "execution", "version": __version__}

    @app.get("/readyz")
    async def readyz(request: Request) -> JSONResponse:
        session_factory = request.app.state.session_factory
        async with session_factory() as session:
            ready = await database_ready(session)
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "unavailable"},
        )

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/internal/v1/plans", dependencies=[internal])
    async def create_plan(request: Request, body: PreparePlanRequest) -> dict[str, Any]:
        plan = await execution(request).create_plan(
            subject=body.subject,
            action=body.action,
            parameters=body.parameters,
            idempotency_key=body.idempotency_key,
            request_id=body.request_id or request_id_var.get(),
        )
        return plan.model_dump(mode="json")

    @app.get("/internal/v1/plans", dependencies=[internal])
    async def list_plans(
        request: Request,
        subject: str = Query(min_length=1),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        plans = await execution(request).list_plans(subject, limit=limit, offset=offset)
        return {"items": [plan.model_dump(mode="json") for plan in plans]}

    @app.get("/internal/v1/plans/{plan_id}", dependencies=[internal])
    async def get_plan(request: Request, plan_id: str, subject: str = Query(min_length=1)) -> dict[str, Any]:
        view = await execution(request).get_plan(plan_id, subject)
        return view.model_dump(mode="json")

    @app.post("/internal/v1/plans/{plan_id}/approve", dependencies=[internal])
    async def approve_plan(request: Request, plan_id: str, body: ApproveRequest) -> dict[str, Any]:
        plan, operation = await execution(request).approve_plan(
            plan_id=plan_id,
            subject=body.subject,
            digest=body.digest,
            note=body.note,
            client_fingerprint=body.client_fingerprint,
            request_id=body.request_id or request_id_var.get(),
        )
        return {
            "plan": plan.model_dump(mode="json"),
            "operation": operation.model_dump(mode="json"),
        }

    @app.post("/internal/v1/plans/{plan_id}/reject", dependencies=[internal])
    async def reject_plan(request: Request, plan_id: str, body: RejectRequest) -> dict[str, Any]:
        plan = await execution(request).reject_plan(
            plan_id=plan_id,
            subject=body.subject,
            request_id=body.request_id or request_id_var.get(),
        )
        return plan.model_dump(mode="json")

    @app.post("/internal/v1/quotes", dependencies=[internal])
    async def quote(request: Request, body: QuoteRequest) -> dict[str, Any]:
        result = await execution(request).quote(subject=body.subject, params=body.parameters)
        return result.model_dump(mode="json")

    @app.get("/internal/v1/resources", dependencies=[internal])
    async def resources(
        request: Request,
        subject: str = Query(min_length=1),
        kind: str = Query(default="all"),
        account_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        return await execution(request).list_resources(subject=subject, kind=kind, account_id=account_id)

    @app.get("/internal/v1/usage/forecast", dependencies=[internal])
    async def usage_forecast(request: Request, subject: str = Query(min_length=1)) -> dict[str, Any]:
        return await execution(request).forecast_current_usage(subject)

    @app.get("/internal/v1/credentials/status", dependencies=[internal])
    async def credentials_status(request: Request, subject: str = Query(min_length=1)) -> dict[str, Any]:
        return await execution(request).credentials_status(subject)

    @app.get("/internal/v1/cloud/health", dependencies=[internal])
    async def cloud_health(request: Request, subject: str = Query(min_length=1)) -> dict[str, Any]:
        return await execution(request).cloud_health(subject)

    @app.get("/internal/v1/plans/{plan_id}/operation", dependencies=[internal])
    async def plan_operation(request: Request, plan_id: str, subject: str = Query(min_length=1)) -> dict[str, Any]:
        operation = await execution(request).plan_operation(plan_id, subject)
        return operation.model_dump(mode="json")

    @app.get("/internal/v1/operations", dependencies=[internal])
    async def list_operations(
        request: Request,
        subject: str = Query(min_length=1),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        operations = await execution(request).list_operations(subject, limit=limit, offset=offset)
        return {"items": [operation.model_dump(mode="json") for operation in operations]}

    @app.get("/internal/v1/operations/{operation_id}", dependencies=[internal])
    async def get_operation(request: Request, operation_id: str, subject: str = Query(min_length=1)) -> dict[str, Any]:
        operation = await execution(request).get_operation(operation_id, subject)
        return operation.model_dump(mode="json")

    @app.get("/internal/v1/operations/{operation_id}/events", dependencies=[internal])
    async def operation_events(
        request: Request,
        operation_id: str,
        subject: str = Query(min_length=1),
        after: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        events = await execution(request).operation_events(operation_id, subject, after=after)
        return {"items": [event.model_dump(mode="json") for event in events]}

    @app.post("/internal/v1/operations/{operation_id}/cancel", dependencies=[internal])
    async def cancel_operation(request: Request, operation_id: str, body: SubjectRequest) -> dict[str, Any]:
        operation = await execution(request).cancel_operation(operation_id, body.subject)
        return operation.model_dump(mode="json")

    @app.post("/internal/v1/operations/{operation_id}/reconcile", dependencies=[internal])
    async def reconcile_operation(request: Request, operation_id: str, body: SubjectRequest) -> dict[str, Any]:
        operation = await execution(request).reconcile_operation(operation_id, body.subject)
        return operation.model_dump(mode="json")

    @app.post("/internal/v1/console/exchange", dependencies=[internal])
    async def console_exchange(request: Request, body: ConsoleExchangeRequest) -> dict[str, Any]:
        return await execution(request).console_exchange(subject=body.subject, lease_id=body.lease_id, token=body.token)

    @app.post("/internal/v1/console/lease", dependencies=[internal])
    async def console_lease(request: Request, body: ConsoleLeaseRequest) -> dict[str, Any]:
        launch = await execution(request).console_lease(subject=body.subject, vm_id=body.vm_id)
        return launch.model_dump(mode="json")

    @app.websocket("/internal/v1/console/ws/{session_id}")
    async def console_relay(websocket: WebSocket, session_id: str) -> None:
        import asyncio

        expected = websocket.app.state.settings.internal_token()
        token = websocket.headers.get("x-internal-token")
        subject = websocket.headers.get("x-internal-subject")
        if not token or not subject or not constant_time_equals(token, expected):
            await websocket.close(code=4401)
            return
        service = websocket.app.state.execution
        try:
            payload = await service.console_endpoint(session_id, subject)
        except Exception:
            await websocket.close(code=4404)
            return
        endpoint = str(payload["endpoint"])
        if endpoint.startswith("https://"):
            endpoint = "wss://" + endpoint[len("https://") :]
        elif endpoint.startswith("http://"):
            endpoint = "ws://" + endpoint[len("http://") :]
        await websocket.accept()
        try:
            from websockets.asyncio.client import connect

            async with connect(
                endpoint,
                subprotocols=["binary"],  # type: ignore[list-item]
                max_size=websocket.app.state.settings.console.max_message_bytes,
                open_timeout=15,
                close_timeout=5,
            ) as upstream:

                async def browser_to_upstream() -> None:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            await upstream.close()
                            return
                        if message.get("text") is not None:
                            await upstream.send(message["text"])
                        elif message.get("bytes") is not None:
                            await upstream.send(message["bytes"])

                async def upstream_to_browser() -> None:
                    async for message in upstream:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)

                tasks = [
                    asyncio.create_task(browser_to_upstream()),
                    asyncio.create_task(upstream_to_browser()),
                ]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                for task in done:
                    exc = task.exception()
                    if exc and not isinstance(exc, asyncio.CancelledError):
                        raise exc
        except Exception:
            try:
                await websocket.close(code=1011)
            except Exception:
                pass
            return
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    return app
