"""Operation history, event stream, cancellation, and reconciliation routes."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from conductor.api.deps import (
    APIServices,
    RequestContext,
    current_principal,
    require_csrf,
    services,
)
from conductor.models.enums import OperationState

router = APIRouter(prefix="/api/v1", tags=["operations"])

_TERMINAL = {
    OperationState.SUCCEEDED,
    OperationState.FAILED,
    OperationState.CANCELLED,
    OperationState.PARTIALLY_SUCCEEDED,
}


@router.get("/operations")
async def list_operations(
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return await services_bundle.execution.list_operations(principal.subject, limit=limit, offset=offset)


@router.get("/operations/{operation_id}")
async def get_operation(
    operation_id: str,
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
) -> dict:
    operation = await services_bundle.execution.get_operation(operation_id, principal.subject)
    return operation


@router.get("/operations/{operation_id}/events")
async def list_operation_events(
    operation_id: str,
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
    after: int = Query(default=0, ge=0),
) -> dict:
    return await services_bundle.execution.operation_events(operation_id, principal.subject, after=after)


def _sse(event_id: int, event: str, data: dict) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


@router.get("/operations/{operation_id}/stream")
async def stream_operation(
    request: Request,
    operation_id: str,
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    header = request.headers.get("Last-Event-ID")
    if header and header.isdigit():
        after = max(after, int(header))

    async def generator() -> AsyncIterator[str]:
        last = after
        started = time.monotonic()
        while time.monotonic() - started < 1800:
            operation = await services_bundle.execution.get_operation(operation_id, principal.subject)
            payload = await services_bundle.execution.operation_events(operation_id, principal.subject, after=last)
            events = payload.get("items", [])
            for event in events:
                last = max(last, int(event["event_id"]))
                yield _sse(int(event["event_id"]), "operation", event)
            state = operation.get("state")
            if state in {item.value for item in _TERMINAL} and not events:
                return
            await asyncio.sleep(0.5)
        yield _sse(last + 1, "error", {"code": "stream_timeout"})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/operations/{operation_id}/cancel")
async def cancel_operation(
    operation_id: str,
    context: RequestContext = Depends(require_csrf),
    services_bundle: APIServices = Depends(services),
) -> dict:
    return await services_bundle.execution.cancel_operation(operation_id, context.principal.subject)


@router.post("/operations/{operation_id}/reconcile")
async def reconcile_operation(
    operation_id: str,
    context: RequestContext = Depends(require_csrf),
    services_bundle: APIServices = Depends(services),
) -> dict:
    return await services_bundle.execution.reconcile_operation(operation_id, context.principal.subject)
