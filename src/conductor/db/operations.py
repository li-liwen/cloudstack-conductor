"""Plan, approval, and operation persistence.

State changes that can race (approval, execution, cancellation, reconciliation)
take a row lock so two workers cannot execute the same approved plan.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from conductor.db.base import utcnow
from conductor.db.models import (
    ApprovalRow,
    OperationEventRow,
    OperationRow,
    PlanRow,
)
from conductor.errors import ConflictError, NotFoundError
from conductor.models.enums import ActionType, ApprovalRequirement, OperationState
from conductor.models.operation import Operation, OperationEvent, OperationStep
from conductor.models.plan import ActionPlan, Approval, PlanEffect, PlanResource
from conductor.models.pricing import PriceQuote

_MUTABLE_OPERATION_FIELDS = (
    "state",
    "updated_at",
    "started_at",
    "finished_at",
    "approval_id",
    "cloudstack_job_id",
    "resource_ids",
    "steps",
    "result",
    "error_code",
    "error_message",
    "attempt",
    "cancel_requested_at",
    "needs_reconciliation_reason",
    "request_id",
)


def _plan(row: PlanRow) -> ActionPlan:
    return ActionPlan(
        plan_id=row.plan_id,
        principal_subject=row.principal_subject,
        tenant_key=row.tenant_key,
        cloud_id=row.cloud_id,
        account_id=row.account_id,
        project_id=row.project_id,
        action=ActionType(row.action),
        parameters=dict(row.parameters or {}),
        resources=[PlanResource.model_validate(item) for item in (row.resources or [])],
        quote=PriceQuote.model_validate(row.quote) if row.quote else None,
        pricing_version=row.pricing_version,
        preconditions=list(row.preconditions or []),
        effects=[PlanEffect.model_validate(item) for item in (row.effects or [])],
        downtime_seconds=row.downtime_seconds,
        data_effects=list(row.data_effects or []),
        approval_requirement=ApprovalRequirement(row.approval_requirement),
        state=row.state,  # type: ignore[arg-type]
        digest=row.digest,
        created_at=row.created_at,
        expires_at=row.expires_at,
        explanation_zh=row.explanation_zh,
        explanation_en=row.explanation_en,
        request_id=row.request_id,
        idempotency_key=row.idempotency_key,
    )


def _approval(row: ApprovalRow | None) -> Approval | None:
    if row is None:
        return None
    return Approval(
        approval_id=row.approval_id,
        plan_id=row.plan_id,
        digest=row.digest,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        expires_at=row.expires_at,
        method=row.method,  # type: ignore[arg-type]
        note=row.note,
        client_fingerprint=row.client_fingerprint,
    )


def _operation(row: OperationRow) -> Operation:
    return Operation(
        operation_id=row.operation_id,
        plan_id=row.plan_id,
        principal_subject=row.principal_subject,
        tenant_key=row.tenant_key,
        cloud_id=row.cloud_id,
        account_id=row.account_id,
        project_id=row.project_id,
        action=ActionType(row.action),
        state=OperationState(row.state),
        created_at=row.created_at,
        updated_at=row.updated_at,
        approval_id=row.approval_id,
        plan_digest=row.plan_digest,
        idempotency_key=row.idempotency_key,
        cloudstack_job_id=row.cloudstack_job_id,
        resource_ids=list(row.resource_ids or []),
        steps=[OperationStep.model_validate(item) for item in (row.steps or [])],
        result=dict(row.result or {}),
        error_code=row.error_code,
        error_message=row.error_message,
        attempt=row.attempt,
        started_at=row.started_at,
        finished_at=row.finished_at,
        cancel_requested_at=row.cancel_requested_at,
        needs_reconciliation_reason=row.needs_reconciliation_reason,
        request_id=row.request_id,
    )


def _event(row: OperationEventRow) -> OperationEvent:
    return OperationEvent(
        event_id=row.event_id,
        operation_id=row.operation_id,
        state=OperationState(row.state),
        message_zh=row.message_zh,
        message_en=row.message_en,
        created_at=row.created_at,
        data=dict(row.data or {}),
    )


class PlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, plan: ActionPlan) -> ActionPlan:
        row = PlanRow(
            plan_id=plan.plan_id,
            principal_subject=plan.principal_subject,
            tenant_key=plan.tenant_key,
            cloud_id=plan.cloud_id,
            account_id=plan.account_id,
            project_id=plan.project_id,
            action=plan.action.value,
            parameters=plan.parameters,
            resources=[item.model_dump(mode="json") for item in plan.resources],
            quote=plan.quote.model_dump(mode="json") if plan.quote else None,
            pricing_version=plan.pricing_version,
            preconditions=list(plan.preconditions),
            effects=[item.model_dump(mode="json") for item in plan.effects],
            downtime_seconds=plan.downtime_seconds,
            data_effects=list(plan.data_effects),
            approval_requirement=plan.approval_requirement.value,
            state=plan.state,
            digest=plan.digest,
            explanation_zh=plan.explanation_zh,
            explanation_en=plan.explanation_en,
            request_id=plan.request_id,
            idempotency_key=plan.idempotency_key,
            created_at=plan.created_at,
            expires_at=plan.expires_at,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("an identical request is already being prepared") from exc
        return plan

    async def get(self, plan_id: str) -> ActionPlan | None:
        row = await self.session.get(PlanRow, plan_id)
        return _plan(row) if row else None

    async def get_for_update(self, plan_id: str) -> ActionPlan | None:
        result = await self.session.execute(select(PlanRow).where(PlanRow.plan_id == plan_id).with_for_update())
        row = result.scalar_one_or_none()
        return _plan(row) if row else None

    async def find_by_idempotency(self, principal_subject: str, idempotency_key: str) -> ActionPlan | None:
        result = await self.session.execute(
            select(PlanRow).where(
                PlanRow.principal_subject == principal_subject,
                PlanRow.idempotency_key == idempotency_key,
            )
        )
        row = result.scalar_one_or_none()
        return _plan(row) if row else None

    async def set_state(self, plan_id: str, state: str) -> None:
        row = await self.session.get(PlanRow, plan_id)
        if row is None:
            raise NotFoundError("plan not found")
        row.state = state
        await self.session.flush()

    async def expire_overdue(self, now: datetime | None = None) -> int:
        now = now or utcnow()
        result = await self.session.execute(
            select(PlanRow).where(PlanRow.state == "awaiting_approval", PlanRow.expires_at <= now)
        )
        count = 0
        for row in result.scalars().all():
            row.state = "expired"
            count += 1
        if count:
            await self.session.flush()
        return count

    async def list_for_principal(self, principal_subject: str, *, limit: int = 50, offset: int = 0) -> list[ActionPlan]:
        result = await self.session.execute(
            select(PlanRow)
            .where(PlanRow.principal_subject == principal_subject)
            .order_by(PlanRow.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_plan(row) for row in result.scalars().all()]

    async def save_approval(self, approval: Approval) -> Approval:
        row = ApprovalRow(
            approval_id=approval.approval_id,
            plan_id=approval.plan_id,
            digest=approval.digest,
            approved_by=approval.approved_by,
            approved_at=approval.approved_at,
            expires_at=approval.expires_at,
            method=approval.method,
            note=approval.note,
            client_fingerprint=approval.client_fingerprint,
            plan_digest=approval.digest,
        )
        self.session.add(row)
        await self.session.flush()
        return approval

    async def get_approval(self, plan_id: str) -> Approval | None:
        result = await self.session.execute(select(ApprovalRow).where(ApprovalRow.plan_id == plan_id))
        return _approval(result.scalar_one_or_none())

    async def get_approval_for_update(self, plan_id: str) -> Approval | None:
        result = await self.session.execute(select(ApprovalRow).where(ApprovalRow.plan_id == plan_id).with_for_update())
        return _approval(result.scalar_one_or_none())

    async def count_by_state(self) -> dict[str, int]:
        result = await self.session.execute(select(PlanRow.state, func.count(PlanRow.plan_id)).group_by(PlanRow.state))
        return {state: int(count) for state, count in result.all()}


class OperationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, operation: Operation) -> Operation:
        row = OperationRow(operation_id=operation.operation_id, plan_id=operation.plan_id)
        self._apply(row, operation)
        row.created_at = operation.created_at
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("an identical operation already exists") from exc
        return operation

    @staticmethod
    def _apply(row: OperationRow, operation: Operation) -> None:
        row.principal_subject = operation.principal_subject
        row.tenant_key = operation.tenant_key
        row.cloud_id = operation.cloud_id
        row.account_id = operation.account_id
        row.project_id = operation.project_id
        row.action = operation.action
        row.state = operation.state.value
        row.approval_id = operation.approval_id
        row.plan_digest = operation.plan_digest
        row.idempotency_key = operation.idempotency_key
        row.cloudstack_job_id = operation.cloudstack_job_id
        row.resource_ids = list(operation.resource_ids)
        row.steps = [step.model_dump(mode="json") for step in operation.steps]
        row.result = operation.result
        row.error_code = operation.error_code
        row.error_message = operation.error_message
        row.attempt = operation.attempt
        row.started_at = operation.started_at
        row.finished_at = operation.finished_at
        row.cancel_requested_at = operation.cancel_requested_at
        row.needs_reconciliation_reason = operation.needs_reconciliation_reason
        row.request_id = operation.request_id
        row.updated_at = operation.updated_at

    async def save(self, operation: Operation) -> Operation:
        row = await self.session.get(OperationRow, operation.operation_id)
        if row is None:
            raise NotFoundError("operation not found")
        self._apply(row, operation)
        await self.session.flush()
        return operation

    async def get(self, operation_id: str) -> Operation | None:
        row = await self.session.get(OperationRow, operation_id)
        return _operation(row) if row else None

    async def get_for_update(self, operation_id: str) -> Operation | None:
        result = await self.session.execute(
            select(OperationRow).where(OperationRow.operation_id == operation_id).with_for_update()
        )
        row = result.scalar_one_or_none()
        return _operation(row) if row else None

    async def find_by_idempotency(self, principal_subject: str, idempotency_key: str) -> Operation | None:
        result = await self.session.execute(
            select(OperationRow).where(
                OperationRow.principal_subject == principal_subject,
                OperationRow.idempotency_key == idempotency_key,
            )
        )
        row = result.scalar_one_or_none()
        return _operation(row) if row else None

    async def latest_for_plan(self, plan_id: str) -> Operation | None:
        result = await self.session.execute(
            select(OperationRow)
            .where(OperationRow.plan_id == plan_id)
            .order_by(OperationRow.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return _operation(row) if row else None

    async def list_for_principal(self, principal_subject: str, *, limit: int = 50, offset: int = 0) -> list[Operation]:
        result = await self.session.execute(
            select(OperationRow)
            .where(OperationRow.principal_subject == principal_subject)
            .order_by(OperationRow.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_operation(row) for row in result.scalars().all()]

    async def list_all(self, *, limit: int = 100, offset: int = 0) -> list[Operation]:
        result = await self.session.execute(
            select(OperationRow).order_by(OperationRow.created_at.desc()).limit(limit).offset(offset)
        )
        return [_operation(row) for row in result.scalars().all()]

    async def transition(
        self,
        operation_id: str,
        *,
        expected: Iterable[OperationState],
        new_state: OperationState,
        message_zh: str,
        message_en: str,
        data: dict | None = None,
        mutate: Callable[[Operation], None] | None = None,
    ) -> Operation:
        """Lock, verify the current state, apply an optional mutation, persist."""

        current = await self.get_for_update(operation_id)
        if current is None:
            raise NotFoundError("operation not found")
        if current.state not in set(expected):
            raise ConflictError(f"operation is {current.state.value}, not {', '.join(s.value for s in expected)}")
        if mutate is not None:
            mutate(current)
        current.state = new_state
        current.updated_at = utcnow()
        if new_state is OperationState.RUNNING and current.started_at is None:
            current.started_at = current.updated_at
        if new_state.terminal and current.finished_at is None:
            current.finished_at = current.updated_at
        await self.save(current)
        await self.append_event(
            operation_id,
            new_state,
            message_zh,
            message_en,
            data=data,
        )
        return current

    async def append_event(
        self,
        operation_id: str,
        state: OperationState,
        message_zh: str,
        message_en: str,
        *,
        data: dict | None = None,
    ) -> OperationEvent:
        row = OperationEventRow(
            operation_id=operation_id,
            state=state.value,
            message_zh=message_zh,
            message_en=message_en,
            data=data or {},
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return _event(row)

    async def events(self, operation_id: str, *, after_id: int = 0, limit: int = 500) -> list[OperationEvent]:
        result = await self.session.execute(
            select(OperationEventRow)
            .where(
                OperationEventRow.operation_id == operation_id,
                OperationEventRow.event_id > after_id,
            )
            .order_by(OperationEventRow.event_id)
            .limit(limit)
        )
        return [_event(row) for row in result.scalars().all()]

    async def count_by_state(self) -> dict[str, int]:
        result = await self.session.execute(
            select(OperationRow.state, func.count(OperationRow.operation_id)).group_by(OperationRow.state)
        )
        return {state: int(count) for state, count in result.all()}


def step_from_dict(item: dict) -> OperationStep:
    return OperationStep.model_validate(item)


def terminal_steps() -> list[OperationStep]:
    return []
