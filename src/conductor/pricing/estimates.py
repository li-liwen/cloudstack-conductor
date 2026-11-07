"""Cost estimates derived from an immutable quote.

An estimate always carries completeness, pricing version, generation time,
and the billing disclaimer. When the quote is unpriced the estimate is
partial, never a guessed amount.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from conductor.models.common import ensure_utc, stable_json, utcnow
from conductor.models.enums import PriceStatus
from conductor.models.pricing import CostEstimate, PriceQuote
from conductor.models.usage import BillingPeriod
from conductor.pricing.engine import money4
from conductor.pricing.periods import billable_hours, period_containing

_DISPLAY = Decimal("0.01")


def _display(value: Decimal) -> Decimal:
    return value.quantize(_DISPLAY, rounding=ROUND_HALF_UP)


def estimate_period(
    quote: PriceQuote,
    period: BillingPeriod,
    *,
    now: datetime | None = None,
    mode: str = "full",
    locale: str = "zh-CN",
) -> CostEstimate:
    """Estimate a full period or the remainder of it.

    ``mode="remaining"`` is a forecast: it assumes the quoted resource state
    continues until the period ends. Full-period figures answer "what would a
    whole month cost at this rate", not "what has been billed".
    """

    now = ensure_utc(now) if now else utcnow()
    hours = billable_hours(period, now, mode=mode)
    compute = _display(money4(quote.compute_hourly * hours))
    storage = _display(money4(quote.storage_hourly * hours))
    discount = _display(money4(quote.discount_hourly * hours))
    total = _display(compute + storage - discount)
    completeness = "complete"
    assumptions: list[str] = []
    if quote.status is not PriceStatus.PRICED:
        completeness = "partial"
        assumptions.append(
            "该规格尚未定价，金额不完整。" if locale != "en" else "This shape is unpriced; totals are incomplete."
        )
    if mode == "remaining":
        assumptions.append(
            "按当前资源状态持续到本周期结束估算。"
            if locale != "en"
            else "Assumes the current resource state continues until the period ends."
        )
    else:
        assumptions.append(
            "按完整自然周期与时价估算，不代表已产生账单。"
            if locale != "en"
            else "Full calendar period at the hourly rate; not an incurred bill."
        )
    assumptions.append(
        "计算费用在关机后停止，存储费用持续到磁盘删除。"
        if locale != "en"
        else "Compute charges stop when an instance stops; storage charges continue until deletion."
    )
    payload = {
        "quote_id": quote.quote_id,
        "pricing_version": quote.pricing_version,
        "period": [period.kind, period.start_date.isoformat(), period.end_date.isoformat()],
        "mode": mode,
        "hours": str(hours),
    }
    estimate_id = "e_" + hashlib.sha256(stable_json(payload).encode()).hexdigest()[:24]
    return CostEstimate(
        estimate_id=estimate_id,
        period="forecast" if mode == "remaining" else ("half_year" if period.kind == "half_year" else "month"),
        period_label=period.label,
        currency=quote.currency,
        starts_at=period.starts_at,
        ends_at=period.ends_at,
        billable_hours=hours,
        compute=compute,
        storage=storage,
        discount=discount,
        total=total,
        completeness=completeness,  # type: ignore[arg-type]
        pricing_version=quote.pricing_version,
        generated_at=now,
        assumptions=assumptions,
    )


def monthly_reference(quote: PriceQuote, now: datetime, *, locale: str = "zh-CN") -> CostEstimate:
    return estimate_period(
        quote,
        period_containing(now, kind="month", locale=locale),
        now=now,
        mode="full",
        locale=locale,
    )


def monthly_forecast(quote: PriceQuote, now: datetime, *, locale: str = "zh-CN") -> CostEstimate:
    return estimate_period(
        quote,
        period_containing(now, kind="month", locale=locale),
        now=now,
        mode="remaining",
        locale=locale,
    )


def half_year_reference(quote: PriceQuote, now: datetime, *, locale: str = "zh-CN") -> CostEstimate:
    return estimate_period(
        quote,
        period_containing(now, kind="half_year", locale=locale),
        now=now,
        mode="full",
        locale=locale,
    )


def half_year_forecast(quote: PriceQuote, now: datetime, *, locale: str = "zh-CN") -> CostEstimate:
    return estimate_period(
        quote,
        period_containing(now, kind="half_year", locale=locale),
        now=now,
        mode="remaining",
        locale=locale,
    )


def quote_summary(quote: PriceQuote, now: datetime, *, locale: str = "zh-CN") -> dict[str, object]:
    """Compact numbers for cards. All values are decimal strings."""

    month = monthly_reference(quote, now, locale=locale)
    half = half_year_reference(quote, now, locale=locale)
    return {
        "currency": quote.currency,
        "hourly": format(quote.total_hourly, "f"),
        "monthly": format(month.total, "f"),
        "half_year": format(half.total, "f"),
        "status": quote.status.value,
        "partial": quote.status is not PriceStatus.PRICED,
        "pricing_version": quote.pricing_version,
    }
