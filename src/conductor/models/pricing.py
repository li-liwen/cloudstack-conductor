"""Pricing, quote, and estimate contracts.

All arithmetic happens in :class:`decimal.Decimal`. Unit rates carry six
decimal places, hourly amounts four, and displayed period totals two. Models
serialize money as decimal strings.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from conductor.models.common import Money, ensure_utc
from conductor.models.enums import PriceStatus

OverrideScope = Literal["account", "domain"]
PeriodKind = Literal["month", "half_year", "forecast"]


class ComputeShape(BaseModel):
    """The billable shape of a service offering.

    ``root_disk_gib`` is the default storage included with the offering. It is
    priced as storage, never folded into a single combined compute rate.
    """

    model_config = ConfigDict(frozen=True)

    cpu_count: int = Field(ge=1, le=512)
    memory_gib: Decimal = Field(gt=0, le=8192)
    gpu_count: int = Field(default=0, ge=0, le=32)
    # CloudStack offerings may include default SSD and/or HDD storage. Both are
    # priced at their own unit rate; there is no combined "included storage"
    # amount that could hide a storage charge inside a compute rate.
    ssd_gib: Decimal = Field(default=Decimal(0), ge=0, le=1_048_576)
    hdd_gib: Decimal = Field(default=Decimal(0), ge=0, le=1_048_576)
    cpu_speed_mhz: int | None = Field(default=None, ge=0)
    name: str | None = None

    @field_validator("memory_gib", "ssd_gib", "hdd_gib", mode="before")
    @classmethod
    def _decimal(cls, value: object) -> Decimal:
        if isinstance(value, float):
            return Decimal(repr(value))
        return Decimal(str(value))

    @property
    def total_storage_gib(self) -> Decimal:
        return self.ssd_gib + self.hdd_gib

    def describe(self) -> str:
        parts = [f"{self.cpu_count} vCPU", f"{format(self.memory_gib.normalize(), 'f')} GiB"]
        if self.gpu_count:
            parts.append(f"{self.gpu_count} GPU")
        if self.ssd_gib:
            parts.append(f"{format(self.ssd_gib.normalize(), 'f')} GiB SSD")
        if self.hdd_gib:
            parts.append(f"{format(self.hdd_gib.normalize(), 'f')} GiB HDD")
        return " / ".join(parts)


class RateCard(BaseModel):
    """A versioned tariff of unit rates.

    Unit rates are per vCPU-hour, per GiB-hour, or per GPU-hour. A rate card is
    immutable once published; creating a new tariff creates a new version.
    """

    model_config = ConfigDict(frozen=True)

    version: str
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    effective_from: datetime
    effective_to: datetime | None = None
    cpu_hour: Money
    memory_gib_hour: Money
    gpu_hour: Money = Decimal(0)
    ssd_gib_hour: Money
    hdd_gib_hour: Money
    source_checksum: str | None = None
    notes: str | None = None

    @field_validator("effective_from", "effective_to")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value else None

    @model_validator(mode="after")
    def _window(self) -> RateCard:
        if self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        for name in ("cpu_hour", "memory_gib_hour", "gpu_hour", "ssd_gib_hour", "hdd_gib_hour"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        return self

    def applies_at(self, when: datetime) -> bool:
        when = ensure_utc(when)
        return self.effective_from <= when and (self.effective_to is None or when < self.effective_to)


class OfferingBinding(BaseModel):
    """Binds a CloudStack service offering UUID to a billable shape.

    Names are descriptive only and are never used for price lookup: duplicate
    names are common and mutable.
    """

    model_config = ConfigDict(frozen=True)

    cloud_id: str
    offering_id: str
    offering_name: str
    shape: ComputeShape
    rate_card_version: str
    currency: str = "CNY"
    effective_from: datetime
    effective_to: datetime | None = None
    mapping_version: str = "1"
    source_reference: str | None = None
    notes: str | None = None

    @field_validator("effective_from", "effective_to")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value else None

    @model_validator(mode="after")
    def _window(self) -> OfferingBinding:
        if self.effective_to and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self

    def applies_at(self, when: datetime) -> bool:
        when = ensure_utc(when)
        return self.effective_from <= when and (self.effective_to is None or when < self.effective_to)


class PriceOverride(BaseModel):
    """Effective-dated account or domain discount for one cloud.

    Discounts apply to compute only. Storage is never discounted, so a
    compute-only discount cannot silently reduce a retained volume's price.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    cloud_id: str
    scope: OverrideScope
    scope_id: str
    discount_percent: Decimal = Field(ge=0, le=100)
    effective_from: datetime
    effective_to: datetime | None = None
    version: str = "1"
    note: str | None = None
    created_by: str | None = None

    @field_validator("effective_from", "effective_to")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value else None

    @field_validator("discount_percent", mode="before")
    @classmethod
    def _decimal(cls, value: object) -> Decimal:
        return Decimal(str(value))

    def applies_at(self, when: datetime) -> bool:
        when = ensure_utc(when)
        return self.effective_from <= when and (self.effective_to is None or when < self.effective_to)


class QuoteLine(BaseModel):
    kind: Literal["compute", "storage", "discount"]
    label: str
    quantity: Decimal
    unit: str
    unit_rate: Money
    amount: Money
    detail: str | None = None


class PriceQuote(BaseModel):
    """A fully explained hourly quote."""

    quote_id: str
    cloud_id: str
    account_id: str
    offering_id: str
    offering_name: str
    shape: ComputeShape | None = None
    currency: str
    rate_card_version: str
    pricing_version: str
    effective_at: datetime
    compute_hourly: Money
    storage_hourly: Money
    discount_hourly: Money
    total_hourly: Money
    status: PriceStatus
    line_items: list[QuoteLine] = Field(default_factory=list)
    unpriced_reasons: list[str] = Field(default_factory=list)
    override_id: str | None = None
    notes: list[str] = Field(default_factory=list)

    @field_validator("effective_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def _consistent(self) -> PriceQuote:
        if self.status is PriceStatus.PRICED and self.unpriced_reasons:
            raise ValueError("a priced quote must not carry unpriced reasons")
        if self.total_hourly < 0:
            raise ValueError("total_hourly must not be negative")
        return self

    @property
    def priced(self) -> bool:
        return self.status is PriceStatus.PRICED and not self.unpriced_reasons


class QuoteRequest(BaseModel):
    offering_id: str
    account_id: str
    project_id: str | None = None
    network_ids: list[str] = Field(default_factory=list)
    disk_size_gib: Decimal | None = None
    when: datetime | None = None


class CostEstimate(BaseModel):
    """A displayable estimate with explicit completeness and disclaimer."""

    estimate_id: str
    period: PeriodKind
    period_label: str
    currency: str
    starts_at: datetime
    ends_at: datetime
    billable_hours: Decimal
    compute: Money
    storage: Money
    discount: Money
    total: Money
    completeness: Literal["complete", "partial", "provisional"]
    pricing_version: str
    generated_at: datetime
    assumptions: list[str] = Field(default_factory=list)
    disclaimer_zh: str = "费用预估，仅供参考，以最终账单为准。"
    disclaimer_en: str = "This is an estimate for reference only; the final invoice prevails."

    @field_validator("starts_at", "ends_at", "generated_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class PriceSnapshot(BaseModel):
    """Immutable copy of the quote attached to an approved plan."""

    quote: PriceQuote
    captured_at: datetime

    @field_validator("captured_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)
