"""Calendar billing periods displayed in Asia/Shanghai.

Timestamps are stored and exchanged in UTC. Month and half-year boundaries are
computed in Asia/Shanghai, where CloudStack Conductor's operators and tenants
read invoices.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from conductor.models.common import ensure_utc
from conductor.models.usage import BillingPeriod

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _midnight_shanghai(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=SHANGHAI).astimezone(UTC)


def month_bounds(year: int, month: int) -> tuple[date, date]:
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def half_year_bounds(year: int, half: int) -> tuple[date, date]:
    if half not in (1, 2):
        raise ValueError("half must be 1 or 2")
    if half == 1:
        return date(year, 1, 1), date(year, 6, 30)
    return date(year, 7, 1), date(year, 12, 31)


def month_period(year: int, month: int, locale: str = "zh-CN") -> BillingPeriod:
    start_date, end_date = month_bounds(year, month)
    end_exclusive = end_date + timedelta(days=1)
    starts_at = _midnight_shanghai(start_date)
    ends_at = _midnight_shanghai(end_exclusive)
    if locale == "en":
        label = f"{calendar.month_name[month]} {year}"
    else:
        label = f"{year}年{month}月"
    return BillingPeriod(
        kind="month",
        label=label,
        start_date=start_date,
        end_date=end_date,
        starts_at=starts_at,
        ends_at=ends_at,
    )


def half_year_period(year: int, half: int, locale: str = "zh-CN") -> BillingPeriod:
    start_date, end_date = half_year_bounds(year, half)
    end_exclusive = end_date + timedelta(days=1)
    starts_at = _midnight_shanghai(start_date)
    ends_at = _midnight_shanghai(end_exclusive)
    if locale == "en":
        label = f"H{half} {year}"
    else:
        label = f"{year}年{'上' if half == 1 else '下'}半年"
    return BillingPeriod(
        kind="half_year",
        label=label,
        start_date=start_date,
        end_date=end_date,
        starts_at=starts_at,
        ends_at=ends_at,
    )


def period_containing(when: datetime, *, kind: str = "month", locale: str = "zh-CN") -> BillingPeriod:
    local = ensure_utc(when).astimezone(SHANGHAI)
    if kind == "month":
        return month_period(local.year, local.month, locale)
    if kind == "half_year":
        return half_year_period(local.year, 1 if local.month <= 6 else 2, locale)
    raise ValueError("kind must be 'month' or 'half_year'")


def next_period(period: BillingPeriod, *, locale: str = "zh-CN") -> BillingPeriod:
    if period.kind == "month":
        local = ensure_utc(period.ends_at).astimezone(SHANGHAI)
        return month_period(local.year, local.month, locale)
    return half_year_period(period.end_date.year + 1, 1, locale)


def hours_between(start: datetime, end: datetime) -> Decimal:
    start_utc, end_utc = ensure_utc(start), ensure_utc(end)
    if end_utc <= start_utc:
        return Decimal(0)
    seconds = Decimal(str((end_utc - start_utc).total_seconds()))
    return (seconds / Decimal(3600)).quantize(Decimal("0.0001"))


def billable_hours(period: BillingPeriod, now: datetime, *, mode: str = "remaining") -> Decimal:
    """Hours in a period, either in full or remaining from ``now``."""

    if mode == "full":
        return hours_between(period.starts_at, period.ends_at)
    if mode != "remaining":
        raise ValueError("mode must be 'full' or 'remaining'")
    start = max(ensure_utc(now), ensure_utc(period.starts_at))
    end = ensure_utc(period.ends_at)
    return hours_between(start, end)


def period_bounds_by_kind(when: datetime, kind: str, locale: str = "zh-CN") -> BillingPeriod:
    return period_containing(when, kind=kind, locale=locale)
