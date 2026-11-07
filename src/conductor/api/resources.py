"""Resource, quote, documentation, console, and connection routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from conductor.api.deps import (
    APIServices,
    RequestContext,
    current_principal,
    require_csrf,
    services,
)
from conductor.db.knowledge import KnowledgeRepository
from conductor.db.mcp import MCPRepository
from conductor.errors import AuthenticationError, NotFoundError
from conductor.knowledge.search import KnowledgeSearch
from conductor.models.common import utcnow
from conductor.models.pricing import PriceQuote, QuoteRequest
from conductor.pricing.estimates import (
    half_year_reference,
    monthly_forecast,
    monthly_reference,
    quote_summary,
)
from conductor.pricing.periods import period_containing

router = APIRouter(prefix="/api/v1", tags=["resources"])
CONSOLE_SALT = "conductor-console-v1"


class ConsoleRequest(BaseModel):
    pass


@router.get("/resources")
async def resources(
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
    kind: str = Query(default="all"),
) -> dict:
    return await services_bundle.execution.resources(subject=principal.subject, kind=kind)


@router.get("/instances/{instance_id}")
async def get_instance(
    instance_id: str,
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
) -> dict:
    payload = await services_bundle.execution.resources(subject=principal.subject, kind="instance")
    items = payload.get("items", {}).get("instances", [])
    instance = next((item for item in items if item.get("id") == instance_id), None)
    if instance is None:
        raise NotFoundError("the instance was not found")
    return instance


@router.post("/quotes")
async def quote(
    body: QuoteRequest,
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
) -> dict:
    parameters = body.model_dump(mode="json", exclude_none=True)
    result = await services_bundle.execution.quote(subject=principal.subject, parameters=parameters)
    price = PriceQuote.model_validate(result)
    now = utcnow()
    month = monthly_reference(price, now, locale="zh-CN")
    month_en = monthly_reference(price, now, locale="en")
    half = half_year_reference(price, now, locale="zh-CN")
    forecast = monthly_forecast(price, now, locale="zh-CN")
    return {
        "quote": result,
        "summary": quote_summary(price, now),
        "estimates": {
            "month_zh": month.model_dump(mode="json"),
            "month_en": month_en.model_dump(mode="json"),
            "half_year_zh": half.model_dump(mode="json"),
            "forecast_zh": forecast.model_dump(mode="json"),
        },
        "current_period": period_containing(now, kind="month").model_dump(mode="json"),
    }


@router.post("/instances/{instance_id}/console")
async def open_console(
    instance_id: str,
    context: RequestContext = Depends(require_csrf),
    services_bundle: APIServices = Depends(services),
) -> dict:
    launch = await services_bundle.execution.console_lease(subject=context.principal.subject, vm_id=instance_id)
    return launch


# -- console launch and relay ---------------------------------------------


class _ConsoleCookieCodec:
    def __init__(self, secret: str) -> None:
        from itsdangerous import URLSafeTimedSerializer

        self._serializer = URLSafeTimedSerializer(secret, salt=CONSOLE_SALT)

    def encode(self, session_id: str, subject: str) -> str:
        return self._serializer.dumps({"sid": session_id, "sub": subject})

    def decode(self, token: str) -> tuple[str, str]:
        from itsdangerous import BadSignature, SignatureExpired

        try:
            payload = self._serializer.loads(token, max_age=1800)
        except (BadSignature, SignatureExpired) as exc:
            raise AuthenticationError("the console session is not valid") from exc
        return str(payload["sid"]), str(payload["sub"])


def _console_codec(services_bundle: APIServices) -> _ConsoleCookieCodec:
    return _ConsoleCookieCodec(services_bundle.settings.session_secret())


@router.get("/console/launch/{lease_id}")
async def console_launch(
    lease_id: str,
    token: str = Query(min_length=10),
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
) -> RedirectResponse:
    result = await services_bundle.execution.console_exchange(subject=principal.subject, lease_id=lease_id, token=token)
    session_id = result["session_id"]
    response = RedirectResponse(f"/console/{session_id}", status_code=303)
    response.set_cookie(
        services_bundle.settings.console_session_cookie,
        _console_codec(services_bundle).encode(session_id, principal.subject),
        max_age=1800,
        httponly=True,
        secure=services_bundle.settings.auth.secure_cookies,
        samesite="lax",
        path="/api/v1/console",
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.websocket("/console/ws/{session_id}")
async def console_ws(websocket: WebSocket, session_id: str) -> None:
    services_bundle: APIServices = websocket.scope["app"].state.services
    token = websocket.cookies.get(services_bundle.settings.console_session_cookie)
    if not token:
        await websocket.close(code=4401)
        return
    try:
        cookie_session, subject = _console_codec(services_bundle).decode(token)
        if cookie_session != session_id:
            raise AuthenticationError("console session mismatch")
        browser_session = services_bundle.session_codec.decode(
            websocket.cookies.get(services_bundle.settings.auth.session_cookie_name) or ""
        )
        if browser_session.subject != subject:
            raise AuthenticationError("console session subject mismatch")
    except AuthenticationError:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        from websockets.asyncio.client import connect

        upstream_url = (
            services_bundle.settings.execution_base_url.rstrip("/")
            .replace("http://", "ws://")
            .replace("https://", "wss://")
            + f"/internal/v1/console/ws/{session_id}"
        )
        headers = {
            "X-Internal-Token": services_bundle.settings.internal_token(),
            "X-Internal-Subject": subject,
        }
        try:
            async with connect(
                upstream_url,
                additional_headers=headers,
                max_size=services_bundle.settings.console.max_message_bytes,
                open_timeout=10,
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

                import asyncio

                tasks = [
                    asyncio.create_task(browser_to_upstream()),
                    asyncio.create_task(upstream_to_browser()),
                ]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                for task in done:
                    exc = task.exception()
                    if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                        raise exc
        except Exception:
            await websocket.close(code=1011)
            return
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# -- documentation ---------------------------------------------------------


@router.get("/docs")
async def list_docs(
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        documents = await KnowledgeRepository(db).list_documents()
    return {"items": [document.model_dump(mode="json") for document in documents]}


@router.get("/docs/search")
async def search_docs(
    q: str = Query(min_length=1, max_length=2000),
    limit: int = Query(default=8, ge=1, le=20),
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        response = await KnowledgeSearch(db, services_bundle.settings).search(q, limit=limit)
    return response.model_dump(mode="json")


@router.get("/docs/{document_id}")
async def get_doc(
    document_id: str,
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        repository = KnowledgeRepository(db)
        document = await repository.get_document(document_id)
        chunks = await repository.chunks_for_document(document_id) if document else []
    if document is None:
        raise NotFoundError("the document was not found")
    payload = document.model_dump(mode="json")
    payload["chunks"] = [chunk.model_dump(mode="json") for chunk in chunks]
    return payload


# -- MCP connections -------------------------------------------------------


@router.get("/connections")
async def list_connections(
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        repo = MCPRepository(db)
        connections = await repo.list_for(principal.subject)
        items = []
        for connection in connections:
            audit = await repo.audit_for(connection.connection_id, limit=20)
            items.append(
                {
                    "connection": connection.model_dump(mode="json"),
                    "audit": [entry.model_dump(mode="json") for entry in audit],
                }
            )
    return {"items": items}


@router.delete("/connections/{connection_id}")
async def revoke_connection(
    connection_id: str,
    context: RequestContext = Depends(require_csrf),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        repo = MCPRepository(db)
        revoked = await repo.revoke(connection_id, principal_subject=context.principal.subject)
        if revoked:
            await repo.append_audit(
                connection_id=connection_id,
                event="revoked",
                outcome="success",
                detail="revoked by the user",
            )
        await db.commit()
    if not revoked:
        raise NotFoundError("the connection was not found")
    return {"ok": True}
