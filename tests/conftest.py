"""Shared test fixtures.

All fixtures are synthetic. No fixture contains an operational address,
credential, tenant name, or real price.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from conductor.models.pricing import ComputeShape, OfferingBinding, PriceOverride, RateCard
from conductor.pricing.engine import PricingCatalog


@pytest.fixture
def when() -> datetime:
    return datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def rate_card(when: datetime) -> RateCard:
    return RateCard(
        version="test-2026-11",
        currency="CNY",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        cpu_hour=Decimal("0.02"),
        memory_gib_hour=Decimal("0.01"),
        gpu_hour=Decimal("1.5"),
        ssd_gib_hour=Decimal("0.001"),
        hdd_gib_hour=Decimal("0.0005"),
        notes="synthetic test tariff",
    )


@pytest.fixture
def shape() -> ComputeShape:
    return ComputeShape(cpu_count=2, memory_gib=Decimal("4"), ssd_gib=Decimal("40"))


@pytest.fixture
def binding(rate_card: RateCard, shape: ComputeShape, when: datetime) -> OfferingBinding:
    return OfferingBinding(
        cloud_id="cloud-demo",
        offering_id="11111111-1111-4111-8111-111111111111",
        offering_name="demo.small",
        shape=shape,
        rate_card_version=rate_card.version,
        currency="CNY",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def catalog(rate_card: RateCard, binding: OfferingBinding) -> PricingCatalog:
    return PricingCatalog(rate_cards=(rate_card,), bindings=(binding,), version="catalog-1")


@pytest.fixture
def domain_override() -> PriceOverride:
    return PriceOverride(
        id="ovr-domain",
        cloud_id="cloud-demo",
        scope="domain",
        scope_id="domain-root",
        discount_percent=Decimal("10"),
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        version="2",
    )


@pytest.fixture
def account_override() -> PriceOverride:
    return PriceOverride(
        id="ovr-account",
        cloud_id="cloud-demo",
        scope="account",
        scope_id="account-demo",
        discount_percent=Decimal("25"),
        effective_from=datetime(2026, 6, 1, tzinfo=UTC),
        version="1",
    )
