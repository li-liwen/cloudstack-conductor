"""Console leases and sessions.

The native CloudStack console token never leaves the broker. Browsers receive a
single-use, user-bound launch URL that expires after 60 seconds.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from conductor.models.common import ensure_utc
from conductor.models.enums import ConsoleLeaseState


class ConsoleLease(BaseModel):
    lease_id: str
    vm_id: str
    vm_name: str | None = None
    principal_subject: str
    tenant_key: str
    account_id: str
    state: ConsoleLeaseState = ConsoleLeaseState.ISSUED
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None
    launch_token_hash: str | None = None
    session_id: str | None = None
    native_endpoint_hash: str | None = None

    @field_validator("created_at", "expires_at", "consumed_at", "revoked_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value else None


class ConsoleLaunch(BaseModel):
    launch_url: str
    expires_at: datetime
    vm_id: str
    vm_name: str | None = None

    @field_validator("expires_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class ConsoleSession(BaseModel):
    session_id: str
    vm_id: str
    ws_path: str
    expires_at: datetime
    viewer: str = "novnc"

    @field_validator("expires_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class ConsoleConnectTarget(BaseModel):
    """Validated upstream for the server-side WebSocket relay."""

    host: str
    port: int
    path: str
    scheme: str = Field(default="wss", pattern="^(wss|ws)$")
    headers: dict[str, str] = Field(default_factory=dict)
