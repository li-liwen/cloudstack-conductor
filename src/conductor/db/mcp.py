"""MCP connection persistence and audit trail."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from conductor.db.base import new_id, utcnow
from conductor.db.models import MCPAuditRow, MCPConnectionRow
from conductor.models.common import ensure_utc
from conductor.models.enums import MCPConnectionState, Scope
from conductor.models.mcp import MCPAuditEntry, MCPConnection


def _connection(row: MCPConnectionRow) -> MCPConnection:
    return MCPConnection(
        connection_id=row.connection_id,
        principal_subject=row.principal_subject,
        cloud_id=row.cloud_id,
        account_id=row.account_id,
        project_id=row.project_id,
        client_id=row.client_id,
        client_name=row.client_name,
        scopes=[Scope(scope) for scope in (row.scopes or [])],
        state=MCPConnectionState(row.state),
        created_at=ensure_utc(row.created_at),
        last_used_at=ensure_utc(row.last_used_at) if row.last_used_at else None,
        expires_at=ensure_utc(row.expires_at),
        revoked_at=ensure_utc(row.revoked_at) if row.revoked_at else None,
        revoked_reason=row.revoked_reason,
    )


def _audit(row: MCPAuditRow) -> MCPAuditEntry:
    return MCPAuditEntry(
        entry_id=row.entry_id,
        connection_id=row.connection_id,
        occurred_at=ensure_utc(row.occurred_at),
        event=row.event,
        detail=row.detail,
        tool_name=row.tool_name,
        outcome=row.outcome,
    )


class MCPRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_connection(
        self,
        *,
        principal_subject: str,
        cloud_id: str,
        account_id: str,
        client_id: str,
        client_name: str,
        scopes: list[Scope],
        project_id: str | None = None,
        authorization_lifetime_days: int = 7,
        token_issued_at: datetime | None = None,
    ) -> MCPConnection:
        now = utcnow()
        latest_result = await self.session.execute(
            select(MCPConnectionRow)
            .where(
                MCPConnectionRow.principal_subject == principal_subject,
                MCPConnectionRow.client_id == client_id,
            )
            .order_by(MCPConnectionRow.created_at.desc())
            .limit(1)
        )
        latest = latest_result.scalar_one_or_none()
        if latest is not None and latest.state == MCPConnectionState.ACTIVE.value:
            if ensure_utc(latest.expires_at) > now:
                latest.scopes = [scope.value for scope in scopes]
                latest.client_name = client_name
                latest.last_used_at = now
                latest.account_id = account_id
                latest.project_id = project_id
                await self.session.flush()
                return _connection(latest)
        if latest is not None and latest.state in {
            MCPConnectionState.REVOKED.value,
            MCPConnectionState.EXPIRED.value,
        }:
            # A revoked client must obtain a fresh authorization (a token
            # issued after revocation) before a new connection is recorded.
            revoked_at = ensure_utc(latest.revoked_at) if latest.revoked_at else None
            if token_issued_at is None or revoked_at is None or token_issued_at <= revoked_at:
                return _connection(latest)
        row = MCPConnectionRow(
            principal_subject=principal_subject,
            cloud_id=cloud_id,
            account_id=account_id,
            project_id=project_id,
            client_id=client_id,
            client_name=client_name,
            scopes=[scope.value for scope in scopes],
            state=MCPConnectionState.ACTIVE.value,
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=authorization_lifetime_days),
        )
        self.session.add(row)
        await self.session.flush()
        return _connection(row)

    async def list_for(self, principal_subject: str) -> list[MCPConnection]:
        result = await self.session.execute(
            select(MCPConnectionRow)
            .where(MCPConnectionRow.principal_subject == principal_subject)
            .order_by(MCPConnectionRow.created_at.desc())
        )
        return [_connection(row) for row in result.scalars().all()]

    async def get(self, connection_id: str) -> MCPConnection | None:
        row = await self.session.get(MCPConnectionRow, connection_id)
        return _connection(row) if row else None

    async def get_for(self, connection_id: str, principal_subject: str) -> MCPConnection | None:
        result = await self.session.execute(
            select(MCPConnectionRow).where(
                MCPConnectionRow.connection_id == connection_id,
                MCPConnectionRow.principal_subject == principal_subject,
            )
        )
        row = result.scalar_one_or_none()
        return _connection(row) if row else None

    async def active_for_client(self, principal_subject: str, client_id: str) -> MCPConnection | None:
        result = await self.session.execute(
            select(MCPConnectionRow)
            .where(
                MCPConnectionRow.principal_subject == principal_subject,
                MCPConnectionRow.client_id == client_id,
                MCPConnectionRow.state == MCPConnectionState.ACTIVE.value,
            )
            .order_by(MCPConnectionRow.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return _connection(row) if row else None

    async def touch(self, connection_id: str) -> None:
        row = await self.session.get(MCPConnectionRow, connection_id)
        if row is not None:
            row.last_used_at = utcnow()
            await self.session.flush()

    async def revoke(self, connection_id: str, *, principal_subject: str, reason: str | None = None) -> bool:
        result = await self.session.execute(
            select(MCPConnectionRow).where(
                MCPConnectionRow.connection_id == connection_id,
                MCPConnectionRow.principal_subject == principal_subject,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        row.state = MCPConnectionState.REVOKED.value
        row.revoked_at = utcnow()
        row.revoked_reason = (reason or "revoked by user")[:500]
        await self.session.flush()
        return True

    async def append_audit(
        self,
        *,
        connection_id: str,
        event: str,
        tool_name: str | None = None,
        outcome: str | None = None,
        detail: str | None = None,
    ) -> MCPAuditEntry:
        row = MCPAuditRow(
            entry_id=new_id("mcpa"),
            connection_id=connection_id,
            event=event,
            tool_name=tool_name,
            outcome=outcome,
            detail=detail[:500] if detail else None,
        )
        self.session.add(row)
        await self.session.flush()
        return _audit(row)

    async def audit_for(self, connection_id: str, *, limit: int = 50) -> list[MCPAuditEntry]:
        result = await self.session.execute(
            select(MCPAuditRow)
            .where(MCPAuditRow.connection_id == connection_id)
            .order_by(MCPAuditRow.occurred_at.desc())
            .limit(limit)
        )
        return [_audit(row) for row in result.scalars().all()]
