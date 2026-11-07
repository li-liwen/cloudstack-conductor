"""Usage-collection and estimate contracts.

Usage is partitioned by cloud/account/resource/period and reported in UTC.
Display periods are computed in Asia/Shanghai elsewhere. A gap is reported,
never reconstructed from present state.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from conductor.models.common import Money, ensure_utc
from conductor.models.enums import UsageCompleteness


class UsageRecord(BaseModel):
    record_id: str
    cloud_id: str
    account_id: str
    zone_id: str | None = None
    resource_id: str
    resource_name: str | None = None
    resource_type: str
    usage_type: int
    description: str | None = None
    offering_id: str | None = None
    start_at: datetime
    end_at: datetime
    quantity: Decimal | float
    unit: str | None = None
    offered_rate: Money | None = None
    collected_at: datetime
    window_id: str

    @field_validator("start_at", "end_at", "collected_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("quantity", mode="before")
    @classmethod
    def _quantity(cls, value: object) -> Decimal:
        from decimal import Decimal as D

        return D(str(value))


class UsageGap(BaseModel):
    start_at: datetime
    end_at: datetime
    reason: str

    @field_validator("start_at", "end_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class UsageCollectionWindow(BaseModel):
    window_id: str
    cloud_id: str
    started_at: datetime
    ended_at: datetime
    status: str
    records_seen: int = 0
    records_stored: int = 0
    duplicates: int = 0
    gaps: list[UsageGap] = Field(default_factory=list)
    created_at: datetime

    @field_validator("started_at", "ended_at", "created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class ResourceUsageSummary(BaseModel):
    resource_id: str
    resource_name: str | None = None
    resource_type: str
    running_hours: Decimal = Decimal(0)
    allocated_hours: Decimal = Decimal(0)
    storage_gib_hours: Decimal = Decimal(0)
    offered_rate_version: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class UsageEstimate(BaseModel):
    account_id: str
    cloud_id: str
    period_start: datetime
    period_end: datetime
    resources: list[ResourceUsageSummary] = Field(default_factory=list)
    compute: Money
    storage: Money
    total: Money
    completeness: UsageCompleteness = UsageCompleteness.PARTIAL
    provisional: bool = False
    pricing_version: str
    generated_at: datetime
    gaps: list[UsageGap] = Field(default_factory=list)
    notes_zh: list[str] = Field(default_factory=list)
    notes_en: list[str] = Field(default_factory=list)
    disclaimer_zh: str = "费用预估，仅供参考，以最终账单为准。"

    @field_validator("period_start", "period_end", "generated_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class BillingPeriod(BaseModel):
    """A calendar month or January–June / July–December half-year in Shanghai."""

    kind: str
    label: str
    start_date: date
    end_date: date
    starts_at: datetime
    ends_at: datetime

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)
