"""Operator-only mapping, pricing, migration, and health views."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from conductor.api.deps import (
    APIServices,
    RequestContext,
    require_operator,
    require_operator_read,
    services,
)
from conductor.db.base import new_id, utcnow
from conductor.db.identity import AuditRepository, IdentityRepository, PasswordMigrationRepository
from conductor.db.knowledge import KnowledgeRepository
from conductor.db.operations import OperationRepository, PlanRepository
from conductor.db.pricing import PricingRepository
from conductor.db.session import database_ready
from conductor.db.usage import UsageRepository
from conductor.models.enums import MappingStage
from conductor.models.pricing import PriceOverride
from conductor.models.principal import IdentityMapping

router = APIRouter(prefix="/api/v1/operator", tags=["operator"])


class MappingUpsertRequest(IdentityMapping):
    pass


class OverrideRequest(BaseModel):
    scope: str = Field(pattern="^(account|domain)$")
    scope_id: str = Field(min_length=1, max_length=64)
    discount_percent: Decimal = Field(ge=0, le=100)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    version: str = Field(default="1", max_length=32)
    note: str | None = Field(default=None, max_length=500)


@router.get("/mappings")
async def list_mappings(
    principal=Depends(require_operator_read),
    services_bundle: APIServices = Depends(services),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    async with services_bundle.session_factory() as db:
        mappings = await IdentityRepository(db).list_mappings(limit=limit, offset=offset)
    return {"items": [mapping.model_dump(mode="json") for mapping in mappings]}


@router.post("/mappings")
async def upsert_mapping(
    body: MappingUpsertRequest,
    context: RequestContext = Depends(require_operator),
    services_bundle: APIServices = Depends(services),
) -> dict:
    # The mapping is authoritative for CloudStack identity; it is only written
    # by an authenticated operator and never derived from a login.
    mapping = IdentityMapping.model_validate(body.model_dump())
    async with services_bundle.session_factory() as db:
        repo = IdentityRepository(db)
        saved = await repo.upsert_mapping(mapping)
        await AuditRepository(db).append(
            actor_subject=context.principal.subject,
            action="operator.mapping.upsert",
            target_type="identity_mapping",
            target_id=saved.subject,
            outcome="success",
        )
        await db.commit()
    return saved.model_dump(mode="json")


@router.post("/mappings/{subject}/stage")
async def set_mapping_stage(
    subject: str,
    stage: MappingStage,
    context: RequestContext = Depends(require_operator),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        await IdentityRepository(db).set_stage(subject, stage)
        await AuditRepository(db).append(
            actor_subject=context.principal.subject,
            action="operator.mapping.stage",
            target_type="identity_mapping",
            target_id=subject,
            outcome="success",
            detail={"stage": stage.value},
        )
        await db.commit()
    return {"ok": True, "stage": stage.value}


@router.get("/pricing")
async def pricing_view(
    principal=Depends(require_operator_read),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        repo = PricingRepository(db)
        cards = await repo.list_cards(principal.cloud_id)
        bindings = await repo.list_bindings(principal.cloud_id)
        overrides = await repo.list_overrides(principal.cloud_id)
        publication = await repo.latest_publication(principal.cloud_id)
    return {
        "publication": (
            {
                "version": publication.version,
                "published_at": publication.published_at.isoformat(),
                "published_by": publication.published_by,
                "checksum": publication.checksum,
            }
            if publication
            else None
        ),
        "rate_cards": [
            {
                "version": row.version,
                "currency": row.currency,
                "effective_from": row.effective_from.isoformat(),
                "effective_to": row.effective_to.isoformat() if row.effective_to else None,
                "cpu_hour": str(row.cpu_hour),
                "memory_gib_hour": str(row.memory_gib_hour),
                "gpu_hour": str(row.gpu_hour),
                "ssd_gib_hour": str(row.ssd_gib_hour),
                "hdd_gib_hour": str(row.hdd_gib_hour),
                "source_checksum": row.source_checksum,
            }
            for row in cards
        ],
        "bindings": [
            {
                "offering_id": row.offering_id,
                "offering_name": row.offering_name,
                "shape": row.shape,
                "rate_card_version": row.rate_card_version,
                "effective_from": row.effective_from.isoformat(),
                "effective_to": row.effective_to.isoformat() if row.effective_to else None,
                "mapping_version": row.mapping_version,
                "source_reference": row.source_reference,
            }
            for row in bindings
        ],
        "overrides": [
            {
                "id": row.id,
                "scope": row.scope,
                "scope_id": row.scope_id,
                "discount_percent": str(row.discount_percent),
                "effective_from": row.effective_from.isoformat(),
                "effective_to": row.effective_to.isoformat() if row.effective_to else None,
                "version": row.version,
            }
            for row in overrides
        ],
    }


@router.post("/pricing/overrides")
async def create_override(
    body: OverrideRequest,
    context: RequestContext = Depends(require_operator),
    services_bundle: APIServices = Depends(services),
) -> dict:
    now = body.effective_from or utcnow()
    override = PriceOverride(
        id=new_id("ovr"),
        cloud_id=context.principal.cloud_id,
        scope=cast(Any, body.scope),
        scope_id=body.scope_id,
        discount_percent=body.discount_percent,
        effective_from=now,
        effective_to=body.effective_to,
        version=body.version,
        note=body.note,
        created_by=context.principal.subject,
    )
    async with services_bundle.session_factory() as db:
        await PricingRepository(db).create_override(override, created_by=context.principal.subject)
        await AuditRepository(db).append(
            actor_subject=context.principal.subject,
            action="operator.pricing.override",
            target_type="price_override",
            target_id=override.id,
            outcome="success",
            detail={"scope": override.scope, "scope_id": override.scope_id},
        )
        await db.commit()
    return override.model_dump(mode="json")


@router.get("/migrations")
async def migrations(
    principal=Depends(require_operator_read),
    services_bundle: APIServices = Depends(services),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    async with services_bundle.session_factory() as db:
        mappings = await IdentityRepository(db).list_mappings(limit=limit)
        rows = []
        repo = PasswordMigrationRepository(db)
        for mapping in mappings:
            row = await repo.get(mapping.subject)
            rows.append(
                {
                    "subject": mapping.subject,
                    "cloudstack_username": mapping.cloudstack_username,
                    "domain": mapping.cloudstack_domain,
                    "mapping_stage": mapping.stage.value,
                    "password_stage": row.stage if row else "unmigrated",
                    "attempts": row.attempts if row else 0,
                    "legacy_2fa": mapping.legacy_2fa,
                    "mfa_required": mapping.mfa_required,
                }
            )
    return {"items": rows}


@router.get("/health")
async def health(
    principal=Depends(require_operator_read),
    services_bundle: APIServices = Depends(services),
) -> dict:
    async with services_bundle.session_factory() as db:
        db_ready = await database_ready(db)
        mappings = await IdentityRepository(db).list_mappings(limit=500)
        plans = await PlanRepository(db).count_by_state()
        operations = await OperationRepository(db).count_by_state()
        docs, chunks = await KnowledgeRepository(db).counts()
        windows = await UsageRepository(db).windows(principal.cloud_id, limit=5)
        audit = await AuditRepository(db).recent(limit=20)
    cloud: dict[str, Any] | None = None
    try:
        cloud = await services_bundle.execution.cloud_health(principal.subject)
    except Exception as exc:
        cloud = {"reachable": False, "code": getattr(exc, "code", "unavailable")}
    return {
        "database": db_ready,
        "execution": cloud,
        "counts": {
            "identity_mappings": len(mappings),
            "plans": plans,
            "operations": operations,
            "knowledge_documents": docs,
            "knowledge_chunks": chunks,
        },
        "usage_windows": [window.model_dump(mode="json") for window in windows],
        "recent_audit": [
            {
                "occurred_at": row.occurred_at.isoformat(),
                "actor": row.actor_subject,
                "action": row.action,
                "outcome": row.outcome,
            }
            for row in audit
        ],
    }


@router.get("/audit")
async def audit_log(
    principal=Depends(require_operator_read),
    services_bundle: APIServices = Depends(services),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    async with services_bundle.session_factory() as db:
        rows = await AuditRepository(db).recent(limit=limit)
    return {
        "items": [
            {
                "id": row.id,
                "occurred_at": row.occurred_at.isoformat(),
                "actor": row.actor_subject,
                "action": row.action,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "outcome": row.outcome,
                "detail": row.detail,
            }
            for row in rows
        ]
    }
