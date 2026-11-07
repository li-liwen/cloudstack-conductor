"""Console lease persistence. The encrypted endpoint is stored server-side."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from conductor.db.base import utcnow
from conductor.db.models import ConsoleLeaseRow
from conductor.models.common import ensure_utc
from conductor.models.console import ConsoleLease
from conductor.models.enums import ConsoleLeaseState


def _lease(row: ConsoleLeaseRow) -> ConsoleLease:
    return ConsoleLease(
        lease_id=row.lease_id,
        vm_id=row.vm_id,
        vm_name=row.vm_name,
        principal_subject=row.principal_subject,
        tenant_key=row.tenant_key,
        account_id=row.account_id,
        state=ConsoleLeaseState(row.state),
        created_at=ensure_utc(row.created_at),
        expires_at=ensure_utc(row.expires_at),
        consumed_at=ensure_utc(row.consumed_at) if row.consumed_at else None,
        revoked_at=ensure_utc(row.revoked_at) if row.revoked_at else None,
        launch_token_hash=row.launch_token_hash,
        session_id=row.session_id,
        native_endpoint_hash=None,
    )


class ConsoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        lease: ConsoleLease,
        *,
        endpoint_ciphertext: str,
        endpoint_key_id: str,
        endpoint_expires_at,
    ) -> ConsoleLease:
        row = ConsoleLeaseRow(
            lease_id=lease.lease_id,
            vm_id=lease.vm_id,
            vm_name=lease.vm_name,
            principal_subject=lease.principal_subject,
            tenant_key=lease.tenant_key,
            account_id=lease.account_id,
            state=lease.state.value,
            created_at=lease.created_at,
            expires_at=lease.expires_at,
            launch_token_hash=lease.launch_token_hash,
            session_id=lease.session_id,
            endpoint_ciphertext=endpoint_ciphertext,
            endpoint_key_id=endpoint_key_id,
            endpoint_expires_at=endpoint_expires_at,
        )
        self.session.add(row)
        await self.session.flush()
        return lease

    async def get(self, lease_id: str) -> ConsoleLease | None:
        row = await self.session.get(ConsoleLeaseRow, lease_id)
        return _lease(row) if row else None

    async def get_for_update(self, lease_id: str) -> ConsoleLease | None:
        result = await self.session.execute(
            select(ConsoleLeaseRow).where(ConsoleLeaseRow.lease_id == lease_id).with_for_update()
        )
        row = result.scalar_one_or_none()
        return _lease(row) if row else None

    async def get_by_launch_hash(self, launch_hash: str) -> ConsoleLease | None:
        result = await self.session.execute(
            select(ConsoleLeaseRow).where(ConsoleLeaseRow.launch_token_hash == launch_hash)
        )
        row = result.scalar_one_or_none()
        return _lease(row) if row else None

    async def consume(self, lease_id: str, *, session_id: str) -> ConsoleLease | None:
        current = await self.get_for_update(lease_id)
        if current is None:
            return None
        row = await self.session.get(ConsoleLeaseRow, lease_id)
        assert row is not None
        row.state = ConsoleLeaseState.CONSUMED.value
        row.consumed_at = utcnow()
        row.session_id = session_id
        await self.session.flush()
        return _lease(row)

    async def mark_expired(self, lease_id: str) -> None:
        row = await self.session.get(ConsoleLeaseRow, lease_id)
        if row is not None and row.state != ConsoleLeaseState.CONSUMED.value:
            row.state = ConsoleLeaseState.EXPIRED.value
            await self.session.flush()

    async def revoke(self, lease_id: str) -> None:
        row = await self.session.get(ConsoleLeaseRow, lease_id)
        if row is not None:
            row.state = ConsoleLeaseState.REVOKED.value
            row.revoked_at = utcnow()
            await self.session.flush()

    async def encrypted_endpoint(self, lease_id: str) -> tuple[str, str, object] | None:
        row = await self.session.get(ConsoleLeaseRow, lease_id)
        if row is None or not row.endpoint_ciphertext or not row.endpoint_key_id:
            return None
        return row.endpoint_ciphertext, row.endpoint_key_id, row.endpoint_expires_at

    async def count_active_for_subject(self, principal_subject: str) -> int:
        result = await self.session.execute(
            select(ConsoleLeaseRow).where(
                ConsoleLeaseRow.principal_subject == principal_subject,
                ConsoleLeaseRow.state == ConsoleLeaseState.ISSUED.value,
            )
        )
        return len(list(result.scalars().all()))
