"""Identity, credentials, password-migration, and audit repositories."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from conductor.db.base import utcnow
from conductor.db.models import (
    AuditEventRow,
    CredentialRow,
    IdentityMappingRow,
    PasswordMigrationRow,
)
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


class IdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_mapping(self, subject: str) -> IdentityMapping | None:
        row = await self.session.get(IdentityMappingRow, subject)
        return _mapping(row) if row else None

    async def get_by_cloudstack_user(self, cloud_id: str, cloudstack_user_id: str) -> IdentityMapping | None:
        result = await self.session.execute(
            select(IdentityMappingRow).where(
                IdentityMappingRow.cloud_id == cloud_id,
                IdentityMappingRow.cloudstack_user_id == cloudstack_user_id,
            )
        )
        row = result.scalar_one_or_none()
        return _mapping(row) if row else None

    async def list_mappings(self, *, limit: int = 200, offset: int = 0) -> list[IdentityMapping]:
        result = await self.session.execute(
            select(IdentityMappingRow)
            .order_by(IdentityMappingRow.cloudstack_domain, IdentityMappingRow.cloudstack_username)
            .limit(limit)
            .offset(offset)
        )
        return [_mapping(row) for row in result.scalars().all()]

    async def upsert_mapping(self, mapping: IdentityMapping) -> IdentityMapping:
        row = await self.session.get(IdentityMappingRow, mapping.subject)
        if row is None:
            row = IdentityMappingRow(subject=mapping.subject)
            self.session.add(row)
        row.cloud_id = mapping.cloud_id
        row.cloudstack_user_id = mapping.cloudstack_user_id
        row.cloudstack_account_id = mapping.cloudstack_account_id
        row.cloudstack_domain_id = mapping.cloudstack_domain_id
        row.cloudstack_domain = mapping.cloudstack_domain
        row.cloudstack_username = mapping.cloudstack_username
        row.account = mapping.account
        row.domain_ancestors = list(mapping.domain_ancestors)
        row.mapping_version = mapping.mapping_version
        row.stage = mapping.stage.value
        row.saml_provider_id = mapping.saml_provider_id
        row.roles = [role.value for role in mapping.roles]
        row.project_ids = list(mapping.project_ids)
        row.delegated_account_ids = list(mapping.delegated_account_ids)
        row.mfa_required = mapping.mfa_required
        row.legacy_2fa = mapping.legacy_2fa
        row.display_name = mapping.display_name
        row.email = mapping.email
        await self.session.flush()
        return _mapping(row)

    async def set_stage(self, subject: str, stage: MappingStage, *, saml_provider_id: str | None = None) -> None:
        row = await self.session.get(IdentityMappingRow, subject)
        if row is None:
            return
        row.stage = stage.value
        if saml_provider_id is not None:
            row.saml_provider_id = saml_provider_id
        await self.session.flush()

    async def count(self) -> int:
        return int(await self.session.scalar(select(func.count(IdentityMappingRow.subject))) or 0)


class CredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, cloud_id: str, cloudstack_user_id: str) -> CredentialRow | None:
        result = await self.session.execute(
            select(CredentialRow).where(
                CredentialRow.cloud_id == cloud_id,
                CredentialRow.cloudstack_user_id == cloudstack_user_id,
            )
        )
        return result.scalar_one_or_none()

    async def save(
        self,
        *,
        cloud_id: str,
        cloudstack_user_id: str,
        cloudstack_account_id: str,
        cloudstack_domain_id: str,
        ciphertext: str,
        key_id: str,
        source: str = "retrieved",
    ) -> CredentialRow:
        row = await self.get(cloud_id, cloudstack_user_id)
        if row is None:
            row = CredentialRow(
                cloud_id=cloud_id,
                cloudstack_user_id=cloudstack_user_id,
                cloudstack_account_id=cloudstack_account_id,
                cloudstack_domain_id=cloudstack_domain_id,
                ciphertext=ciphertext,
                key_id=key_id,
                source=source,
            )
            self.session.add(row)
        else:
            row.ciphertext = ciphertext
            row.key_id = key_id
            row.source = source
            row.disabled = False
        await self.session.flush()
        return row

    async def mark_used(self, cloud_id: str, cloudstack_user_id: str) -> None:
        row = await self.get(cloud_id, cloudstack_user_id)
        if row is not None:
            row.last_used_at = utcnow()
            await self.session.flush()

    async def set_disabled(self, cloud_id: str, cloudstack_user_id: str, disabled: bool) -> None:
        row = await self.get(cloud_id, cloudstack_user_id)
        if row is not None:
            row.disabled = disabled
            await self.session.flush()


class PasswordMigrationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, subject: str) -> PasswordMigrationRow | None:
        return await self.session.get(PasswordMigrationRow, subject)

    async def start(self, subject: str, *, cloudstack_user_id: str, domain_id: str) -> PasswordMigrationRow:
        row = await self.session.get(PasswordMigrationRow, subject)
        if row is None:
            row = PasswordMigrationRow(
                subject=subject,
                stage=MappingStage.UNMIGRATED.value,
                cloudstack_user_id=cloudstack_user_id,
                domain_id=domain_id,
            )
            self.session.add(row)
        await self.session.flush()
        return row

    async def advance(
        self,
        subject: str,
        stage: MappingStage,
        *,
        saml_provider_id: str | None = None,
        evidence_hash: str | None = None,
        legacy_session_closed: bool | None = None,
        error: str | None = None,
    ) -> None:
        row = await self.session.get(PasswordMigrationRow, subject)
        if row is None:
            return
        row.stage = stage.value
        if saml_provider_id is not None:
            row.saml_provider_id = saml_provider_id
        if evidence_hash is not None:
            row.evidence_hash = evidence_hash
        if legacy_session_closed is not None:
            row.legacy_session_closed = legacy_session_closed
        row.last_error = error[:300] if error else None
        await self.session.flush()


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        *,
        action: str,
        outcome: str,
        actor_subject: str | None = None,
        actor_type: str = "user",
        target_type: str | None = None,
        target_id: str | None = None,
        request_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditEventRow:
        row = AuditEventRow(
            actor_subject=actor_subject,
            actor_type=actor_type,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            request_id=request_id,
            detail=detail or {},
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def recent(self, *, limit: int = 100, actor_subject: str | None = None) -> list[AuditEventRow]:
        query = select(AuditEventRow).order_by(AuditEventRow.occurred_at.desc()).limit(limit)
        if actor_subject:
            query = query.where(AuditEventRow.actor_subject == actor_subject)
        result = await self.session.execute(query)
        return list(result.scalars().all())
