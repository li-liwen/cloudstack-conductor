"""Plan preparation and approval routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from conductor.api.deps import (
    APIServices,
    RequestContext,
    current_principal,
    require_csrf,
    services,
)
from conductor.models.plan import ApprovalRequest
from conductor.models.pricing import PriceQuote
from conductor.pricing.estimates import quote_summary

router = APIRouter(prefix="/api/v1", tags=["plans"])


class PlanCreateRequest(BaseModel):
    action: str = Field(min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)


class RejectPlanRequest(BaseModel):
    digest: str | None = None
    note: str | None = Field(default=None, max_length=500)


@router.post("/plans")
async def create_plan(
    body: PlanCreateRequest,
    context: RequestContext = Depends(require_csrf),
    services_bundle: APIServices = Depends(services),
) -> dict:
    plan = await services_bundle.execution.create_plan(
        subject=context.principal.subject,
        action=body.action,
        parameters=body.parameters,
        idempotency_key=body.idempotency_key,
    )
    quote = plan.get("quote")
    summary = None
    if quote:
        price = PriceQuote.model_validate(quote)
        summary = quote_summary(price, _now(), locale="zh-CN")
    return {"plan": plan, "summary": summary}


@router.get("/plans")
async def list_plans(
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return await services_bundle.execution.list_plans(principal.subject, limit=limit, offset=offset)


@router.get("/plans/{plan_id}")
async def get_plan(
    plan_id: str,
    principal=Depends(current_principal),
    services_bundle: APIServices = Depends(services),
) -> dict:
    return await services_bundle.execution.get_plan(plan_id, principal.subject)


@router.post("/plans/{plan_id}/approve")
async def approve_plan(
    plan_id: str,
    body: ApprovalRequest,
    context: RequestContext = Depends(require_csrf),
    services_bundle: APIServices = Depends(services),
) -> dict:
    result = await services_bundle.execution.approve_plan(
        plan_id,
        subject=context.principal.subject,
        digest=body.digest,
        note=body.note,
        client_fingerprint=_fingerprint(context),
    )
    return result


@router.post("/plans/{plan_id}/reject")
async def reject_plan(
    plan_id: str,
    body: RejectPlanRequest,
    context: RequestContext = Depends(require_csrf),
    services_bundle: APIServices = Depends(services),
) -> dict:
    plan = await services_bundle.execution.reject_plan(plan_id, subject=context.principal.subject)
    return {"plan": plan}


def _now():
    from conductor.models.common import utcnow

    return utcnow()


def _fingerprint(context: RequestContext) -> str:
    import hashlib

    return hashlib.sha256(f"{context.principal.subject}:{context.session.csrf}".encode()).hexdigest()[:32]
