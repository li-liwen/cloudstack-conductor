"""SQLAlchemy implementation of the migration store.

Every stage transition and failure record runs in its own short transaction and
takes ``SELECT ... FOR UPDATE`` locks on the mapping row and the password
migration row, in that order. External HTTP calls happen outside those locks;
the compare-and-set in :meth:`advance` is what prevents two requests from both
applying a side effect and advancing the stage.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.db.identity import AuditRepository
from conductor.db.models import IdentityMappingRow, PasswordMigrationRow
from conductor.identity.models import AdvanceResult, MigrationState
from conductor.models.enums import MappingStage, PrincipalRole
from conductor.models.principal import IdentityMapping


def _mapping(row: IdentityMappingRow) -> IdentityMapping:
    return IdentityMapping(
        subject=row.subject,
        cloud_id=row.cloud_id,
        cloudstack_user_id=row.cloudstack_user_id,
        cloudstack_account_id=row.cloudstack_account_id,
        cloudstack_domain_id=row.cloudstack_domain_id,
        cloudstack_domain=row.cloudstack_domain,
        cloudstack_username=row.cloudstack_username,
        account=row.account,
        domain_ancestors=list(row.domain_ancestors or []),
        mapping_version=row.mapping_version,
        stage=MappingStage(row.stage),
        saml_provider_id=row.saml_provider_id,
        roles=[PrincipalRole(role) for role in (row.roles or ["user"])],
        project_ids=list(row.project_ids or []),
        delegated_account_ids=list(row.delegated_account_ids or []),
        mfa_required=row.mfa_required,
        legacy_2fa=row.legacy_2fa,
        display_name=row.display_name,
        email=row.email,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SQLIdentityMigrationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def load(self, subject: str) -> MigrationState | None:
        async with self._session_factory() as session:
            mapping_row = await session.get(IdentityMappingRow, subject)
            if mapping_row is None:
                return None
            migration_row = await session.get(PasswordMigrationRow, subject)
            if migration_row is None:
                return MigrationState(mapping=_mapping(mapping_row), stage=MappingStage(mapping_row.stage))
            return MigrationState(
                mapping=_mapping(mapping_row),
                stage=MappingStage(migration_row.stage),
                attempts=int(migration_row.attempts or 0),
                last_error=migration_row.last_error,
                legacy_session_closed=bool(migration_row.legacy_session_closed),
                evidence_hash=migration_row.evidence_hash,
                saml_provider_id=migration_row.saml_provider_id,
                updated_at=migration_row.updated_at or migration_row.created_at,
            )

    async def advance(
        self,
        subject: str,
        expected: MappingStage,
        target: MappingStage,
        *,
        saml_provider_id: str | None = None,
        evidence_hash: str | None = None,
        legacy_session_closed: bool | None = None,
    ) -> AdvanceResult:
        async with self._session_factory() as session:
            async with session.begin():
                mapping_row = await self._lock_mapping(session, subject)
                if mapping_row is None:
                    return AdvanceResult(False, None)
                migration_row = await self._lock_migration(session, subject)
                current = (
                    MappingStage(migration_row.stage) if migration_row is not None else MappingStage(mapping_row.stage)
                )
                if current is not expected:
                    return AdvanceResult(False, current)
                if migration_row is None:
                    migration_row = self._new_row(mapping_row)
                    session.add(migration_row)
                migration_row.stage = target.value
                if saml_provider_id is not None:
                    migration_row.saml_provider_id = saml_provider_id
                if evidence_hash is not None:
                    migration_row.evidence_hash = evidence_hash
                if legacy_session_closed is not None:
                    migration_row.legacy_session_closed = legacy_session_closed
                mapping_row.stage = target.value
                await session.flush()
                await AuditRepository(session).append(
                    actor_subject=subject,
                    action="identity.migration.advance",
                    target_type="identity_mapping",
                    target_id=subject,
                    outcome="success",
                    detail={
                        "from": current.value,
                        "to": target.value,
                        "evidence_hash": evidence_hash,
                        "saml_provider_id": saml_provider_id,
                    },
                )
            return AdvanceResult(True, target)

    async def record_failure(self, subject: str, code: str) -> None:
        safe_code = _safe_code(code)
        async with self._session_factory() as session, session.begin():
            mapping_row = await self._lock_mapping(session, subject)
            if mapping_row is None:
                return
            migration_row = await self._lock_migration(session, subject)
            if migration_row is None:
                migration_row = self._new_row(mapping_row)
                session.add(migration_row)
            migration_row.attempts = int(migration_row.attempts or 0) + 1
            migration_row.last_error = safe_code
            await session.flush()
            await AuditRepository(session).append(
                actor_subject=subject,
                action="identity.migration.failure",
                target_type="identity_mapping",
                target_id=subject,
                outcome="failure",
                detail={"code": safe_code},
            )

    async def mark_session_closed(self, subject: str, *, closed: bool = True) -> None:
        async with self._session_factory() as session, session.begin():
            mapping_row = await self._lock_mapping(session, subject)
            if mapping_row is None:
                return
            migration_row = await self._lock_migration(session, subject)
            if migration_row is None:
                migration_row = self._new_row(mapping_row)
                session.add(migration_row)
            migration_row.legacy_session_closed = closed
            await session.flush()

    async def audit(
        self,
        subject: str,
        *,
        action: str,
        outcome: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        async with self._session_factory() as session, session.begin():
            await AuditRepository(session).append(
                actor_subject=subject,
                action=action,
                target_type="identity_mapping",
                target_id=subject,
                outcome=outcome,
                detail=detail or {},
            )

    @staticmethod
    async def _lock_mapping(session: AsyncSession, subject: str) -> IdentityMappingRow | None:
        result = await session.execute(
            select(IdentityMappingRow).where(IdentityMappingRow.subject == subject).with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _lock_migration(session: AsyncSession, subject: str) -> PasswordMigrationRow | None:
        result = await session.execute(
            select(PasswordMigrationRow).where(PasswordMigrationRow.subject == subject).with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _new_row(mapping_row: IdentityMappingRow) -> PasswordMigrationRow:
        return PasswordMigrationRow(
            subject=mapping_row.subject,
            stage=mapping_row.stage,
            cloudstack_user_id=mapping_row.cloudstack_user_id,
            domain_id=mapping_row.cloudstack_domain_id,
        )


def _safe_code(code: str) -> str:
    text = "".join(character for character in str(code) if character.isalnum() or character in "._-")
    return (text or "identity_bridge_error")[:300]
