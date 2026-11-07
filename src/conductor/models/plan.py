"""Immutable action plans and human approvals.

A plan is constructed once, quoted, hashed, and then disclosed. Approval is
bound to the digest; any material change produces a different digest and
therefore requires a new approval.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from conductor.models.common import Money, ensure_utc, stable_json
from conductor.models.enums import ActionType, ApprovalRequirement
from conductor.models.pricing import PriceQuote

PlanState = Literal[
    "draft",
    "awaiting_approval",
    "approved",
    "rejected",
    "expired",
    "executed",
    "cancelled",
]


class PlanResource(BaseModel):
    kind: str
    resource_id: str
    name: str | None = None
    account_id: str | None = None
    project_id: str | None = None


class PlanEffect(BaseModel):
    """A disclosed effect of executing a plan."""

    kind: Literal[
        "create",
        "start",
        "stop",
        "reboot",
        "resize",
        "delete",
        "data_loss",
        "network_exposure",
        "cost_change",
        "guest_change",
        "account",
        "none",
    ]
    summary_zh: str
    summary_en: str
    resource_id: str | None = None
    cost_delta_hourly: Money | None = None
    data_at_risk: list[str] = Field(default_factory=list)


class ActionPlan(BaseModel):
    plan_id: str
    principal_subject: str
    tenant_key: str
    cloud_id: str
    account_id: str
    project_id: str | None = None
    action: ActionType
    parameters: dict[str, object] = Field(default_factory=dict)
    resources: list[PlanResource] = Field(default_factory=list)
    quote: PriceQuote | None = None
    pricing_version: str | None = None
    preconditions: list[str] = Field(default_factory=list)
    effects: list[PlanEffect] = Field(default_factory=list)
    downtime_seconds: int | None = Field(default=None, ge=0)
    data_effects: list[str] = Field(default_factory=list)
    approval_requirement: ApprovalRequirement = ApprovalRequirement.REQUIRED
    state: PlanState = "awaiting_approval"
    digest: str = ""
    created_at: datetime
    expires_at: datetime
    explanation_zh: str = ""
    explanation_en: str = ""
    request_id: str | None = None
    idempotency_key: str | None = None

    @field_validator("created_at", "expires_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _time_window(self) -> ActionPlan:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self

    def digest_payload(self) -> dict[str, object]:
        """The material content covered by an approval.

        Explanation text is excluded because rewording never changes what the
        cloud will do. Every operational parameter, resource, quote, and effect
        is included.
        """

        return {
            "plan_id": self.plan_id,
            "principal_subject": self.principal_subject,
            "tenant_key": self.tenant_key,
            "cloud_id": self.cloud_id,
            "account_id": self.account_id,
            "project_id": self.project_id,
            "action": self.action.value,
            "parameters": self.parameters,
            "resources": [resource.model_dump(mode="json") for resource in self.resources],
            "quote": self.quote.model_dump(mode="json") if self.quote else None,
            "pricing_version": self.pricing_version,
            "preconditions": self.preconditions,
            "effects": [effect.model_dump(mode="json") for effect in self.effects],
            "downtime_seconds": self.downtime_seconds,
            "data_effects": self.data_effects,
            "approval_requirement": self.approval_requirement.value,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    def compute_digest(self) -> str:
        return hashlib.sha256(stable_json(self.digest_payload()).encode("utf-8")).hexdigest()

    def with_digest(self) -> ActionPlan:
        return self.model_copy(update={"digest": self.compute_digest()})


class Approval(BaseModel):
    approval_id: str
    plan_id: str
    digest: str
    approved_by: str
    approved_at: datetime
    expires_at: datetime
    method: Literal["browser_session", "operator_override"] = "browser_session"
    note: str | None = None
    client_fingerprint: str | None = None

    @field_validator("approved_at", "expires_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    def valid_for(self, plan: ActionPlan, now: datetime) -> bool:
        return (
            self.plan_id == plan.plan_id
            and self.digest == plan.digest
            and self.approved_by == plan.principal_subject
            and self.expires_at > now
        )


class ApprovalRequest(BaseModel):
    """Browser approval of the exact plan digest.

    A caller cannot approve by supplying ``approved=true`` to a model; this is
    an authenticated application action with a CSRF token and digest match.
    """

    digest: str = Field(min_length=64, max_length=64)
    note: str | None = Field(default=None, max_length=500)


class PlanDecision(BaseModel):
    plan_id: str
    state: PlanState
    digest: str
    message_zh: str
    message_en: str


class QuoteSummary(BaseModel):
    currency: str
    hourly: Money
    monthly: Money
    half_year: Money
    status: str
    partial: bool = False


class PlanView(BaseModel):
    """Public view of a plan. Mirrors :class:`ActionPlan` without internal keys."""

    plan: ActionPlan
    approval: Approval | None = None
    quote_summary: QuoteSummary | None = None
    can_approve: bool = False
    approval_ttl_seconds: int = 0


def decimal_or_none(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))
