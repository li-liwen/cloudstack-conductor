"""Conversation, message, run, and SSE routes."""

from __future__ import annotations

import asyncio
import json
import logging
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
from conductor.db.conversations import ConversationRepository
from conductor.db.models import ConversationRow
from conductor.errors import ConflictError, NotFoundError
from conductor.models.conversation import (
    ChatRequest,
    ConversationCreate,
    MessagePart,
)
from conductor.models.enums import RunState

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["conversations"])

_TERMINAL = {
    RunState.COMPLETED,
    RunState.FAILED,
    RunState.CANCELLED,
    RunState.AWAITING_APPROVAL,
}


@router.get("/conversations")
async def list_conversations(
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    async with services_bundle.session_factory() as db:
        items = await ConversationRepository(db).list_for(principal.subject, limit=limit)
    return {"items": [item.model_dump(mode="json") for item in items]}


@router.post("/conversations")
async def create_conversation(
    body: ConversationCreate,
    context: RequestContext = Depends(require_csrf),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        conversation = await ConversationRepository(db).create(
            principal_subject=context.principal.subject,
            tenant_key=context.principal.tenant_key,
            title=body.title,
            cloud_context={
                "account_id": body.account_id or context.principal.cloudstack_account_id,
                **({"project_id": body.project_id} if body.project_id else {}),
            },
        )
        await db.commit()
    return conversation.model_dump(mode="json")


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        repo = ConversationRepository(db)
        conversation = await repo.get(conversation_id, principal.subject)
        if conversation is None:
            raise NotFoundError("the conversation was not found")
        messages = await repo.messages(conversation_id)
        runs = await repo.list_runs(conversation_id)
    return {
        "conversation": conversation.model_dump(mode="json"),
        "messages": [message.model_dump(mode="json") for message in messages],
        "runs": [run.model_dump(mode="json") for run in runs],
    }


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    body: ChatRequest,
    context: RequestContext = Depends(require_csrf),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        repo = ConversationRepository(db)
        conversation = await repo.get(conversation_id, context.principal.subject)
        if conversation is None:
            raise NotFoundError("the conversation was not found")
        user_message = await repo.add_message(
            conversation_id=conversation_id,
            role="user",
            parts=[MessagePart(type="text", text=body.message)],
        )
        if conversation.title in {"", "新对话", "New conversation"}:
            await repo.rename(conversation_id, context.principal.subject, body.message[:80])
        run = await repo.create_run(
            conversation_id=conversation_id,
            principal_subject=context.principal.subject,
            user_message_id=user_message.message_id,
        )
        row = await db.get(ConversationRow, conversation_id)
        if row is not None:
            row.cloud_context = {
                **(row.cloud_context or {}),
                "locale": body.locale,
                **({"account_id": body.account_id} if body.account_id else {}),
                **({"project_id": body.project_id} if body.project_id else {}),
            }
        await repo.add_event(run.run_id, "queued", {"message_id": user_message.message_id})
        await db.commit()
    try:
        await services_bundle.agent_queue.enqueue(run.run_id)
    except Exception as exc:
        logger.exception("could not enqueue agent run %s", run.run_id)
        async with services_bundle.session_factory() as db:
            repo = ConversationRepository(db)
            await repo.update_run(run.run_id, state=RunState.FAILED, error="queue_unavailable")
            await repo.add_event(run.run_id, "error", {"code": "queue_unavailable"})
            await db.commit()
        raise ConflictError("the assistant is temporarily unavailable; retry shortly") from exc
    return {
        "run": run.model_dump(mode="json"),
        "message": user_message.model_dump(mode="json"),
    }


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        run = await ConversationRepository(db).get_run_for(run_id, principal.subject)
        if run is None:
            raise NotFoundError("the run was not found")
    return run.model_dump(mode="json")


def _format_sse(event_id: int, event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event}\ndata: {payload}\n\n"


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    request: Request,
    run_id: str,
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    last_event_header = request.headers.get("Last-Event-ID")
    if last_event_header and last_event_header.isdigit():
        after = max(after, int(last_event_header))

    async def generator() -> AsyncIterator[str]:
        last = after
        started = time.monotonic()
        idle = 0
        while time.monotonic() - started < 1800:
            async with services_bundle.session_factory() as db:
                repo = ConversationRepository(db)
                run = await repo.get_run_for(run_id, principal.subject)
                if run is None:
                    yield _format_sse(last + 1, "error", {"code": "not_found"})
                    return
                events = await repo.events(run_id, after_id=last, limit=200)
                state = run.state
            for event in events:
                last = max(last, event.event_id)
                yield _format_sse(event.event_id, event.event, event.model_dump(mode="json"))
                idle = 0
            if state in _TERMINAL and not events:
                return
            if not events:
                idle += 1
                if idle % 25 == 0:
                    yield ": keepalive\n\n"
            await asyncio.sleep(0.35)
        yield _format_sse(last + 1, "error", {"code": "stream_timeout"})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.delete("/conversations/{conversation_id}")
async def archive_conversation(
    conversation_id: str,
    context: RequestContext = Depends(require_csrf),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        conversation = await ConversationRepository(db).get(conversation_id, context.principal.subject)
        if conversation is None:
            raise NotFoundError("the conversation was not found")
        row = await db.get(ConversationRow, conversation_id)
        assert row is not None
        row.archived = True
        await db.commit()
    return {"ok": True}
