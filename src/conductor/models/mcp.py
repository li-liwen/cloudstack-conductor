"""MCP connection and consent contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from conductor.models.common import ensure_utc
from conductor.models.enums import MCPConnectionState, Scope


class MCPConnection(BaseModel):
    connection_id: str
    principal_subject: str
    cloud_id: str
    account_id: str
    project_id: str | None = None
    client_id: str
    client_name: str
    scopes: list[Scope] = Field(default_factory=list)
    state: MCPConnectionState = MCPConnectionState.ACTIVE
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime
    revoked_at: datetime | None = None
    revoked_reason: str | None = None
    authorization_lifetime_days: int = 7

    @field_validator("created_at", "last_used_at", "expires_at", "revoked_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value else None

    @property
    def active(self) -> bool:
        return self.state is MCPConnectionState.ACTIVE and self.expires_at > datetime.now(tz=self.expires_at.tzinfo)


class MCPConnectionView(BaseModel):
    """What the Connected agents page may show."""

    connection: MCPConnection
    audit: list[MCPAuditEntry] = Field(default_factory=list)


class MCPAuditEntry(BaseModel):
    entry_id: str
    connection_id: str
    occurred_at: datetime
    event: str
    detail: str | None = None
    tool_name: str | None = None
    outcome: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class ConsentRequest(BaseModel):
    client_id: str
    client_name: str
    scopes: list[Scope]
    requested_at: datetime
    resource: str | None = None

    @field_validator("requested_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class ConsentDecision(BaseModel):
    approved: bool
    scopes: list[Scope] = Field(default_factory=list)
    remember: bool = False
