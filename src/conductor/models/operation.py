"""Durable operation and step contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from conductor.models.common import JsonValue, ensure_utc
from conductor.models.enums import OperationState, StepState


class OperationStep(BaseModel):
    step_id: str
    name: str
    state: StepState = StepState.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail_zh: str | None = None
    detail_en: str | None = None
    cloudstack_job_id: str | None = None
    result: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("started_at", "finished_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value else None


class Operation(BaseModel):
    operation_id: str
    plan_id: str
    principal_subject: str
    tenant_key: str
    cloud_id: str
    account_id: str
    project_id: str | None = None
    action: str
    state: OperationState
    created_at: datetime
    updated_at: datetime
    approval_id: str | None = None
    plan_digest: str
    idempotency_key: str | None = None
    cloudstack_job_id: str | None = None
    resource_ids: list[str] = Field(default_factory=list)
    steps: list[OperationStep] = Field(default_factory=list)
    result: dict[str, JsonValue] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    attempt: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    needs_reconciliation_reason: str | None = None
    request_id: str | None = None

    @field_validator("created_at", "updated_at", "started_at", "finished_at", "cancel_requested_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value else None

    @property
    def terminal(self) -> bool:
        return self.state.terminal


class OperationEvent(BaseModel):
    event_id: int
    operation_id: str
    state: OperationState
    message_zh: str
    message_en: str
    created_at: datetime
    data: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class ReconcileRequest(BaseModel):
    operation_id: str
    make_record_id: str = Field(min_length=1, max_length=200)
