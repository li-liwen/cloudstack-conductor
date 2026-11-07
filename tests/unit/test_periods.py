from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from conductor.pricing.estimates import (
    half_year_forecast,
    half_year_reference,
    monthly_forecast,
    monthly_reference,
)
from conductor.pricing.periods import (
    half_year_period,
    hours_between,
    month_period,
    period_containing,
)


def test_month_boundaries_use_shanghai_midnight() -> None:
    period = month_period(2026, 11)
    # 2026-11-01 00:00 Asia/Shanghai == 2026-10-31 16:00 UTC.
    assert period.starts_at.isoformat() == "2026-10-31T16:00:00+00:00"
    assert period.ends_at.isoformat() == "2026-11-30T16:00:00+00:00"
    assert period.label == "2026年11月"


def test_month_has_calendar_hours() -> None:
    assert hours_between(month_period(2026, 11).starts_at, month_period(2026, 11).ends_at) == Decimal("720.0000")
    assert hours_between(month_period(2026, 2).starts_at, month_period(2026, 2).ends_at) == Decimal("672.0000")


def test_half_year_is_january_to_june_and_july_to_december() -> None:
    first = half_year_period(2026, 1)
    second = half_year_period(2026, 2)
    assert first.label == "2026年上半年"
    assert first.start_date.isoformat() == "2026-01-01"
    assert first.end_date.isoformat() == "2026-06-30"
    assert second.start_date.isoformat() == "2026-07-01"
    assert second.end_date.isoformat() == "2026-12-31"
    assert hours_between(first.starts_at, first.ends_at) == Decimal("4344.0000")


def test_period_containing_uses_shanghai_time() -> None:
    # 2026-12-31 23:30 UTC is 2027-01-01 07:30 in Shanghai.
    period = period_containing(datetime(2026, 12, 31, 23, 30, tzinfo=UTC), kind="month")
    assert period.label == "2027年1月"
    half = period_containing(datetime(2026, 12, 31, 23, 30, tzinfo=UTC), kind="half_year")
    assert half.label == "2027年上半年"


def test_estimates(catalog, when) -> None:
    quote = catalog.quote(
        cloud_id="cloud-demo",
        account_id="account-demo",
        offering_id="11111111-1111-4111-8111-111111111111",
        when=when,
    )
    month = monthly_reference(quote, when)
    assert month.total == Decimal("86.40")  # 0.12 * 720
    assert month.completeness == "complete"
    forecast = monthly_forecast(quote, when)
    assert forecast.period == "forecast"
    assert forecast.billable_hours < month.billable_hours
    half = half_year_reference(quote, when)
    assert half.period == "half_year"
    half_forecast = half_year_forecast(quote, when)
    assert half_forecast.billable_hours <= half.billable_hours
