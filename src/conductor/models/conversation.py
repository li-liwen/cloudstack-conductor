"""Conversation, message, and streamed-run contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from conductor.models.common import JsonValue, ensure_utc
from conductor.models.enums import RunState

MessageRole = Literal["user", "assistant", "system", "tool"]
PartType = Literal["text", "quote", "plan", "approval", "operation", "progress", "resource", "citation"]


class MessagePart(BaseModel):
    type: PartType
    text: str | None = None
    data: dict[str, JsonValue] = Field(default_factory=dict)


class Message(BaseModel):
    message_id: str
    conversation_id: str
    role: MessageRole
    parts: list[MessagePart] = Field(default_factory=list)
    created_at: datetime
    run_id: str | None = None
    citations: list[dict[str, JsonValue]] = Field(default_factory=list)

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class Conversation(BaseModel):
    conversation_id: str
    principal_subject: str
    tenant_key: str
    title: str
    created_at: datetime
    updated_at: datetime
    archived: bool = False
    cloud_context: dict[str, str] = Field(default_factory=dict)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    account_id: str | None = None
    project_id: str | None = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class Run(BaseModel):
    run_id: str
    conversation_id: str
    principal_subject: str
    state: RunState
    created_at: datetime
    updated_at: datetime
    plan_id: str | None = None
    operation_id: str | None = None
    error: str | None = None
    user_message_id: str | None = None
    assistant_message_id: str | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class RunEvent(BaseModel):
    event_id: int
    run_id: str
    event: str
    data: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    account_id: str | None = None
    project_id: str | None = None
    locale: Literal["zh-CN", "en"] = "zh-CN"
