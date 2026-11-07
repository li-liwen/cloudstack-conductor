from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from conductor.errors import ConfigurationError
from conductor.models.enums import PriceStatus
from conductor.models.pricing import OfferingBinding, PriceOverride, RateCard
from conductor.pricing.engine import PricingCatalog


def test_component_arithmetic(catalog: PricingCatalog, when: datetime) -> None:
    quote = catalog.quote(
        cloud_id="cloud-demo",
        account_id="account-demo",
        offering_id="11111111-1111-4111-8111-111111111111",
        when=when,
    )
    # 2 vCPU * 0.02 + 4 GiB * 0.01 = 0.08 compute; 40 GiB SSD * 0.001 = 0.04 storage
    assert quote.status is PriceStatus.PRICED
    assert quote.compute_hourly == Decimal("0.0800")
    assert quote.storage_hourly == Decimal("0.0400")
    assert quote.total_hourly == Decimal("0.1200")
    assert quote.discount_hourly == Decimal("0.0000")
    assert {line.kind for line in quote.line_items} == {
        "compute",
        "storage",
    }


def test_decimal_arithmetic_has_no_binary_noise(catalog: PricingCatalog, when: datetime) -> None:
    quote = catalog.quote(
        cloud_id="cloud-demo",
        account_id="account-demo",
        offering_id="11111111-1111-4111-8111-111111111111",
        when=when,
    )
    assert format(quote.total_hourly, "f") == "0.1200"
    assert isinstance(quote.total_hourly, Decimal)


def test_compute_discount_does_not_discount_storage(
    catalog: PricingCatalog, account_override: PriceOverride, when: datetime
) -> None:
    catalog = PricingCatalog(
        rate_cards=catalog.rate_cards,
        bindings=catalog.bindings,
        overrides=(account_override,),
        version="catalog-2",
    )
    quote = catalog.quote(
        cloud_id="cloud-demo",
        account_id="account-demo",
        offering_id="11111111-1111-4111-8111-111111111111",
        when=when,
    )
    assert quote.compute_hourly == Decimal("0.0800")
    assert quote.storage_hourly == Decimal("0.0400")
    # 25% of compute only.
    assert quote.discount_hourly == Decimal("0.0200")
    assert quote.total_hourly == Decimal("0.1000")


def test_account_override_beats_domain_override(
    catalog: PricingCatalog,
    account_override: PriceOverride,
    domain_override: PriceOverride,
    when: datetime,
) -> None:
    catalog = PricingCatalog(
        rate_cards=catalog.rate_cards,
        bindings=catalog.bindings,
        overrides=(domain_override, account_override),
        version="catalog-3",
    )
    quote = catalog.quote(
        cloud_id="cloud-demo",
        account_id="account-demo",
        offering_id="11111111-1111-4111-8111-111111111111",
        domain_ancestors=["domain-child", "domain-root"],
        when=when,
    )
    assert quote.override_id == "ovr-account"


def test_nearest_domain_override_wins(catalog: PricingCatalog, domain_override: PriceOverride, when: datetime) -> None:
    child = domain_override.model_copy(
        update={"id": "ovr-child", "scope_id": "domain-child", "discount_percent": Decimal("5")}
    )
    catalog = PricingCatalog(
        rate_cards=catalog.rate_cards,
        bindings=catalog.bindings,
        overrides=(domain_override, child),
        version="catalog-4",
    )
    quote = catalog.quote(
        cloud_id="cloud-demo",
        account_id="account-demo",
        offering_id="11111111-1111-4111-8111-111111111111",
        domain_ancestors=["domain-child", "domain-root"],
        when=when,
    )
    assert quote.override_id == "ovr-child"


def test_override_outside_effective_window_ignored(
    catalog: PricingCatalog, account_override: PriceOverride, when: datetime
) -> None:
    expired = account_override.model_copy(
        update={
            "effective_from": datetime(2020, 1, 1, tzinfo=UTC),
            "effective_to": datetime(2021, 1, 1, tzinfo=UTC),
        }
    )
    catalog = PricingCatalog(
        rate_cards=catalog.rate_cards,
        bindings=catalog.bindings,
        overrides=(expired,),
        version="catalog-5",
    )
    quote = catalog.quote(
        cloud_id="cloud-demo",
        account_id="account-demo",
        offering_id="11111111-1111-4111-8111-111111111111",
        when=when,
    )
    assert quote.override_id is None


def test_unpriced_offering_has_no_guessed_price(catalog: PricingCatalog, when: datetime) -> None:
    quote = catalog.quote(
        cloud_id="cloud-demo",
        account_id="account-demo",
        offering_id="22222222-2222-4222-8222-222222222222",
        when=when,
    )
    assert quote.status is PriceStatus.UNPRICED
    assert quote.total_hourly == Decimal(0)
    assert "offering_not_priced" in quote.unpriced_reasons
    assert quote.priced is False


def test_extra_storage_is_charged(catalog: PricingCatalog, when: datetime) -> None:
    quote = catalog.quote(
        cloud_id="cloud-demo",
        account_id="account-demo",
        offering_id="11111111-1111-4111-8111-111111111111",
        when=when,
        extra_ssd_gib=Decimal("10"),
    )
    assert quote.storage_hourly == Decimal("0.0500")
    assert quote.total_hourly == Decimal("0.1300")


def test_overlapping_bindings_are_rejected(rate_card: RateCard, binding: OfferingBinding) -> None:
    overlapping = binding.model_copy(
        update={
            "effective_from": datetime(2026, 6, 1, tzinfo=UTC),
            "mapping_version": "2",
        }
    )
    with pytest.raises(ConfigurationError):
        PricingCatalog(
            rate_cards=(rate_card,),
            bindings=(binding, overlapping),
            version="bad",
        )


def test_missing_rate_card_is_rejected(rate_card: RateCard, binding: OfferingBinding) -> None:
    broken = binding.model_copy(update={"rate_card_version": "missing"})
    with pytest.raises(ConfigurationError):
        PricingCatalog(rate_cards=(rate_card,), bindings=(broken,))


def test_two_offerings_with_same_name_have_distinct_prices(
    catalog: PricingCatalog, rate_card: RateCard, binding: OfferingBinding, when: datetime
) -> None:
    second = binding.model_copy(
        update={
            "offering_id": "33333333-3333-4333-8333-333333333333",
            "shape": binding.shape.model_copy(update={"cpu_count": 4}),
        }
    )
    catalog = PricingCatalog(
        rate_cards=catalog.rate_cards,
        bindings=(binding, second),
        version="catalog-6",
    )
    first_quote = catalog.quote(
        cloud_id="cloud-demo",
        account_id="account-demo",
        offering_id=binding.offering_id,
        when=when,
    )
    second_quote = catalog.quote(
        cloud_id="cloud-demo",
        account_id="account-demo",
        offering_id=second.offering_id,
        when=when,
    )
    assert first_quote.compute_hourly == Decimal("0.0800")
    assert second_quote.compute_hourly == Decimal("0.1200")
    assert first_quote.offering_name == second_quote.offering_name


def test_gpu_component(rate_card: RateCard, binding: OfferingBinding, when: datetime) -> None:
    gpu_shape = binding.shape.model_copy(update={"gpu_count": 1})
    gpu_binding = binding.model_copy(update={"shape": gpu_shape})
    catalog = PricingCatalog(rate_cards=(rate_card,), bindings=(gpu_binding,), version="catalog-gpu")
    quote = catalog.quote(
        cloud_id="cloud-demo",
        account_id="account-demo",
        offering_id=binding.offering_id,
        when=when,
    )
    assert quote.compute_hourly == Decimal("1.5800")
