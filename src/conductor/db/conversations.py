"""Conversation, message, and run persistence with strict owner isolation."""

from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from conductor.db.base import new_id, utcnow
from conductor.db.models import ConversationRow, MessageRow, RunEventRow, RunRow
from conductor.models.conversation import Conversation, Message, MessagePart, MessageRole, Run, RunEvent
from conductor.models.enums import RunState


def _conversation(row: ConversationRow) -> Conversation:
    return Conversation(
        conversation_id=row.conversation_id,
        principal_subject=row.principal_subject,
        tenant_key=row.tenant_key,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived=row.archived,
        cloud_context=dict(row.cloud_context or {}),
    )


def _message(row: MessageRow) -> Message:
    return Message(
        message_id=row.message_id,
        conversation_id=row.conversation_id,
        role=cast(MessageRole, row.role),
        parts=[MessagePart.model_validate(part) for part in (row.parts or [])],
        created_at=row.created_at,
        run_id=row.run_id,
        citations=list(row.citations or []),
    )


def _run(row: RunRow) -> Run:
    return Run(
        run_id=row.run_id,
        conversation_id=row.conversation_id,
        principal_subject=row.principal_subject,
        state=RunState(row.state),
        created_at=row.created_at,
        updated_at=row.updated_at,
        plan_id=row.plan_id,
        operation_id=row.operation_id,
        error=row.error,
        user_message_id=row.user_message_id,
        assistant_message_id=row.assistant_message_id,
    )


def _event(row: RunEventRow) -> RunEvent:
    return RunEvent(
        event_id=row.event_id,
        run_id=row.run_id,
        event=row.event,
        data=dict(row.data or {}),
        created_at=row.created_at,
    )


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        principal_subject: str,
        tenant_key: str,
        title: str | None = None,
        cloud_context: dict[str, str] | None = None,
    ) -> Conversation:
        row = ConversationRow(
            principal_subject=principal_subject,
            tenant_key=tenant_key,
            title=(title or "新对话")[:200],
            cloud_context=cloud_context or {},
        )
        self.session.add(row)
        await self.session.flush()
        return _conversation(row)

    async def list_for(self, principal_subject: str, *, limit: int = 50, offset: int = 0) -> list[Conversation]:
        result = await self.session.execute(
            select(ConversationRow)
            .where(
                ConversationRow.principal_subject == principal_subject,
                ConversationRow.archived.is_(False),
            )
            .order_by(ConversationRow.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_conversation(row) for row in result.scalars().all()]

    async def get(self, conversation_id: str, principal_subject: str) -> Conversation | None:
        result = await self.session.execute(
            select(ConversationRow).where(
                ConversationRow.conversation_id == conversation_id,
                ConversationRow.principal_subject == principal_subject,
            )
        )
        row = result.scalar_one_or_none()
        return _conversation(row) if row else None

    async def rename(self, conversation_id: str, principal_subject: str, title: str) -> bool:
        conversation = await self.get(conversation_id, principal_subject)
        if conversation is None:
            return False
        result = await self.session.execute(
            select(ConversationRow).where(ConversationRow.conversation_id == conversation_id)
        )
        row = result.scalar_one()
        row.title = title[:200]
        row.updated_at = utcnow()
        await self.session.flush()
        return True

    async def add_message(
        self,
        *,
        conversation_id: str,
        role: str,
        parts: list[MessagePart],
        run_id: str | None = None,
        citations: list[dict] | None = None,
        message_id: str | None = None,
    ) -> Message:
        row = MessageRow(
            message_id=message_id or new_id("msg"),
            conversation_id=conversation_id,
            role=role,
            parts=[part.model_dump(mode="json") for part in parts],
            run_id=run_id,
            citations=citations or [],
        )
        self.session.add(row)
        conversation = await self.session.get(ConversationRow, conversation_id)
        if conversation is not None:
            conversation.updated_at = utcnow()
            if not conversation.title and parts and parts[0].text:
                conversation.title = parts[0].text[:80]
        await self.session.flush()
        return _message(row)

    async def messages(self, conversation_id: str, *, limit: int = 200, after: str | None = None) -> list[Message]:
        query = (
            select(MessageRow)
            .where(MessageRow.conversation_id == conversation_id)
            .order_by(MessageRow.created_at, MessageRow.message_id)
            .limit(limit)
        )
        if after:
            query = query.where(MessageRow.message_id > after)
        result = await self.session.execute(query)
        return [_message(row) for row in result.scalars().all()]

    async def create_run(
        self,
        *,
        conversation_id: str,
        principal_subject: str,
        user_message_id: str | None = None,
    ) -> Run:
        row = RunRow(
            conversation_id=conversation_id,
            principal_subject=principal_subject,
            state=RunState.QUEUED.value,
            user_message_id=user_message_id,
        )
        self.session.add(row)
        await self.session.flush()
        return _run(row)

    async def get_run(self, run_id: str) -> Run | None:
        row = await self.session.get(RunRow, run_id)
        return _run(row) if row else None

    async def get_run_for(self, run_id: str, principal_subject: str) -> Run | None:
        result = await self.session.execute(
            select(RunRow).where(RunRow.run_id == run_id, RunRow.principal_subject == principal_subject)
        )
        row = result.scalar_one_or_none()
        return _run(row) if row else None

    async def update_run(
        self,
        run_id: str,
        *,
        state: RunState | None = None,
        plan_id: str | None = None,
        operation_id: str | None = None,
        error: str | None = None,
        assistant_message_id: str | None = None,
    ) -> Run | None:
        row = await self.session.get(RunRow, run_id)
        if row is None:
            return None
        if state is not None:
            row.state = state.value
        if plan_id is not None:
            row.plan_id = plan_id
        if operation_id is not None:
            row.operation_id = operation_id
        if error is not None:
            row.error = error[:2000]
        if assistant_message_id is not None:
            row.assistant_message_id = assistant_message_id
        row.updated_at = utcnow()
        await self.session.flush()
        return _run(row)

    async def add_event(self, run_id: str, event: str, data: dict | None = None) -> RunEvent:
        row = RunEventRow(run_id=run_id, event=event, data=data or {})
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return _event(row)

    async def events(self, run_id: str, *, after_id: int = 0, limit: int = 500) -> list[RunEvent]:
        result = await self.session.execute(
            select(RunEventRow)
            .where(RunEventRow.run_id == run_id, RunEventRow.event_id > after_id)
            .order_by(RunEventRow.event_id)
            .limit(limit)
        )
        return [_event(row) for row in result.scalars().all()]

    async def list_runs(self, conversation_id: str, *, limit: int = 20) -> list[Run]:
        result = await self.session.execute(
            select(RunRow)
            .where(RunRow.conversation_id == conversation_id)
            .order_by(RunRow.created_at.desc())
            .limit(limit)
        )
        return [_run(row) for row in result.scalars().all()]
