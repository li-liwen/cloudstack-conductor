"""Pricing arithmetic.

Rates are per vCPU-hour, per GiB-hour, and per GPU-hour. Storage is priced
separately at its tier rate. Compute discounts never discount storage.
Unpriced offerings are never assigned guessed prices.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from conductor.errors import ConfigurationError, ValidationFailure
from conductor.models.common import ensure_utc, stable_json, utcnow
from conductor.models.enums import PriceStatus
from conductor.models.pricing import (
    ComputeShape,
    OfferingBinding,
    PriceOverride,
    PriceQuote,
    QuoteLine,
    RateCard,
)

_ARG = Decimal("0.0001")
_UNIT = Decimal("0.000001")
_PERCENT = Decimal("100")


def money4(value: Decimal) -> Decimal:
    return value.quantize(_ARG, rounding=ROUND_HALF_UP)


def unit6(value: Decimal) -> Decimal:
    return value.quantize(_UNIT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PricingCatalog:
    """An immutable, versioned pricing context.

    A catalog is loaded from the operator database, never from a request. A
    catalog with overlapping bindings for one offering is rejected so a quote
    can never depend on list order.
    """

    rate_cards: tuple[RateCard, ...]
    bindings: tuple[OfferingBinding, ...]
    overrides: tuple[PriceOverride, ...] = ()
    version: str = "1"
    source_checksum: str | None = None

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        cards: dict[str, RateCard] = {}
        for card in self.rate_cards:
            if card.version in cards:
                raise ConfigurationError(f"duplicate rate card version {card.version}")
            cards[card.version] = card
        active: dict[tuple[str, str], list[OfferingBinding]] = {}
        for binding in self.bindings:
            if binding.rate_card_version not in cards:
                raise ConfigurationError(f"binding {binding.offering_id} references a missing rate card")
            if binding.currency != cards[binding.rate_card_version].currency:
                raise ConfigurationError(f"binding {binding.offering_id} currency does not match its rate card")
            active.setdefault((binding.cloud_id, binding.offering_id), []).append(binding)
        for key, group in active.items():
            ordered = sorted(group, key=lambda item: item.effective_from)
            for earlier, later in zip(ordered, ordered[1:], strict=False):
                if earlier.effective_to is None or earlier.effective_to > later.effective_from:
                    raise ConfigurationError("overlapping effective-dated price bindings for offering " + key[1])
        for override in self.overrides:
            if override.scope not in {"account", "domain"}:
                raise ConfigurationError("unsupported price override scope")

    def rate_card(self, version: str) -> RateCard:
        for card in self.rate_cards:
            if card.version == version:
                return card
        raise ConfigurationError("rate card not found")

    def binding_for(self, cloud_id: str, offering_id: str, when: datetime) -> tuple[OfferingBinding, RateCard] | None:
        when = ensure_utc(when)
        matches = [
            binding
            for binding in self.bindings
            if binding.cloud_id == cloud_id and binding.offering_id == offering_id and binding.applies_at(when)
        ]
        if not matches:
            return None
        matches.sort(key=lambda item: (item.effective_from, item.mapping_version), reverse=True)
        binding = matches[0]
        return binding, self.rate_card(binding.rate_card_version)

    def override_for(
        self,
        *,
        cloud_id: str,
        account_id: str,
        domain_ancestors: list[str] | tuple[str, ...],
        when: datetime,
    ) -> PriceOverride | None:
        when = ensure_utc(when)
        candidates = [
            override for override in self.overrides if override.cloud_id == cloud_id and override.applies_at(when)
        ]
        account_matches = [item for item in candidates if item.scope == "account" and item.scope_id == account_id]
        if account_matches:
            return _latest(account_matches)
        # Nearest applicable domain: the caller supplies the account's domain
        # chain from most-specific to root.
        for domain_id in domain_ancestors:
            domain_matches = [item for item in candidates if item.scope == "domain" and item.scope_id == domain_id]
            if domain_matches:
                return _latest(domain_matches)
        return None

    def quote(
        self,
        *,
        cloud_id: str,
        account_id: str,
        offering_id: str,
        when: datetime | None = None,
        domain_ancestors: list[str] | tuple[str, ...] = (),
        offering_name: str | None = None,
        shape_fallback: ComputeShape | None = None,
        extra_ssd_gib: Decimal = Decimal(0),
        extra_hdd_gib: Decimal = Decimal(0),
    ) -> PriceQuote:
        when = ensure_utc(when) if when else utcnow()
        if extra_ssd_gib < 0 or extra_hdd_gib < 0:
            raise ValidationFailure("extra storage must not be negative")
        found = self.binding_for(cloud_id, offering_id, when)
        if found is None:
            return self._unpriced(
                cloud_id=cloud_id,
                account_id=account_id,
                offering_id=offering_id,
                offering_name=offering_name or offering_id,
                when=when,
                shape=shape_fallback,
                reasons=["offering_not_priced"],
            )
        binding, card = found
        shape = binding.shape.model_copy(
            update={
                "ssd_gib": binding.shape.ssd_gib + extra_ssd_gib,
                "hdd_gib": binding.shape.hdd_gib + extra_hdd_gib,
            }
        )
        if shape.total_storage_gib > Decimal(1_048_576):
            raise ValidationFailure("requested storage is too large")
        override = self.override_for(
            cloud_id=cloud_id,
            account_id=account_id,
            domain_ancestors=domain_ancestors,
            when=when,
        )
        lines: list[QuoteLine] = []
        cpu_rate = unit6(card.cpu_hour)
        mem_rate = unit6(card.memory_gib_hour)
        gpu_rate = unit6(card.gpu_hour)
        ssd_rate = unit6(card.ssd_gib_hour)
        hdd_rate = unit6(card.hdd_gib_hour)

        compute = Decimal(0)
        cpu_amount = money4(Decimal(shape.cpu_count) * cpu_rate)
        compute += cpu_amount
        lines.append(
            QuoteLine(
                kind="compute",
                label="CPU",
                quantity=Decimal(shape.cpu_count),
                unit="vCPU·hour",
                unit_rate=cpu_rate,
                amount=cpu_amount,
                detail=f"{shape.cpu_count} vCPU",
            )
        )
        mem_amount = money4(shape.memory_gib * mem_rate)
        compute += mem_amount
        lines.append(
            QuoteLine(
                kind="compute",
                label="Memory",
                quantity=shape.memory_gib,
                unit="GiB·hour",
                unit_rate=mem_rate,
                amount=mem_amount,
            )
        )
        if shape.gpu_count:
            gpu_amount = money4(Decimal(shape.gpu_count) * gpu_rate)
            compute += gpu_amount
            lines.append(
                QuoteLine(
                    kind="compute",
                    label="GPU",
                    quantity=Decimal(shape.gpu_count),
                    unit="GPU·hour",
                    unit_rate=gpu_rate,
                    amount=gpu_amount,
                )
            )
        compute = money4(compute)

        storage = Decimal(0)
        if shape.ssd_gib:
            amount = money4(shape.ssd_gib * ssd_rate)
            storage += amount
            lines.append(
                QuoteLine(
                    kind="storage",
                    label="SSD storage",
                    quantity=shape.ssd_gib,
                    unit="GiB·hour",
                    unit_rate=ssd_rate,
                    amount=amount,
                )
            )
        if shape.hdd_gib:
            amount = money4(shape.hdd_gib * hdd_rate)
            storage += amount
            lines.append(
                QuoteLine(
                    kind="storage",
                    label="HDD storage",
                    quantity=shape.hdd_gib,
                    unit="GiB·hour",
                    unit_rate=hdd_rate,
                    amount=amount,
                )
            )
        storage = money4(storage)

        discount = Decimal(0)
        if override is not None and override.discount_percent > 0:
            # Compute-only discount. Storage remains fully charged.
            discount = money4(compute * override.discount_percent / _PERCENT)
            lines.append(
                QuoteLine(
                    kind="discount",
                    label="Compute discount",
                    quantity=override.discount_percent,
                    unit="%",
                    unit_rate=Decimal(0),
                    amount=-discount,
                    detail=f"override {override.version}",
                )
            )
        total = money4(compute + storage - discount)
        pricing_version = f"{self.version}:{binding.rate_card_version}:{binding.mapping_version}"
        if override is not None:
            pricing_version += f":{override.id}:{override.version}"
        payload = {
            "cloud_id": cloud_id,
            "account_id": account_id,
            "offering_id": offering_id,
            "shape": shape.model_dump(mode="json"),
            "pricing_version": pricing_version,
            "when": when,
        }
        quote_id = "q_" + hashlib.sha256(stable_json(payload).encode()).hexdigest()[:24]
        return PriceQuote(
            quote_id=quote_id,
            cloud_id=cloud_id,
            account_id=account_id,
            offering_id=offering_id,
            offering_name=binding.offering_name,
            shape=shape,
            currency=binding.currency,
            rate_card_version=binding.rate_card_version,
            pricing_version=pricing_version,
            effective_at=when,
            compute_hourly=compute,
            storage_hourly=storage,
            discount_hourly=discount,
            total_hourly=total,
            status=PriceStatus.PRICED,
            line_items=lines,
            override_id=override.id if override else None,
        )

    def _unpriced(
        self,
        *,
        cloud_id: str,
        account_id: str,
        offering_id: str,
        offering_name: str,
        when: datetime,
        shape: ComputeShape | None,
        reasons: list[str],
    ) -> PriceQuote:
        payload = {
            "cloud_id": cloud_id,
            "account_id": account_id,
            "offering_id": offering_id,
            "reason": reasons,
            "when": when,
        }
        quote_id = "q_" + hashlib.sha256(stable_json(payload).encode()).hexdigest()[:24]
        return PriceQuote(
            quote_id=quote_id,
            cloud_id=cloud_id,
            account_id=account_id,
            offering_id=offering_id,
            offering_name=offering_name,
            shape=shape,
            currency="CNY",
            rate_card_version="unpriced",
            pricing_version="unpriced",
            effective_at=when,
            compute_hourly=Decimal(0),
            storage_hourly=Decimal(0),
            discount_hourly=Decimal(0),
            total_hourly=Decimal(0),
            status=PriceStatus.UNPRICED,
            unpriced_reasons=reasons,
        )

    def storage_quote(
        self,
        *,
        cloud_id: str,
        account_id: str,
        offering_id: str,
        offering_name: str,
        tier: str,
        gib: Decimal,
        when: datetime | None = None,
        domain_ancestors: list[str] | tuple[str, ...] = (),
    ) -> PriceQuote:
        """Price standalone storage (a data volume) without a compute shape."""

        when = ensure_utc(when) if when else utcnow()
        if tier not in {"ssd", "hdd"}:
            raise ValidationFailure("storage tier must be ssd or hdd")
        card = self.current_rate_card(cloud_id, when)
        if card is None or gib < 0:
            return self._unpriced(
                cloud_id=cloud_id,
                account_id=account_id,
                offering_id=offering_id,
                offering_name=offering_name,
                when=when,
                shape=None,
                reasons=["storage_rate_missing"],
            )
        rate = unit6(card.ssd_gib_hour if tier == "ssd" else card.hdd_gib_hour)
        amount = money4(gib * rate)
        override = self.override_for(
            cloud_id=cloud_id,
            account_id=account_id,
            domain_ancestors=domain_ancestors,
            when=when,
        )
        discount = Decimal(0)
        lines = [
            QuoteLine(
                kind="storage",
                label=f"{tier.upper()} storage",
                quantity=gib,
                unit="GiB·hour",
                unit_rate=rate,
                amount=amount,
            )
        ]
        if override is not None and override.discount_percent > 0:
            # A compute-only discount never applies to storage, even when the
            # volume is created for a discounted account.
            lines.append(
                QuoteLine(
                    kind="discount",
                    label="Compute discount (not applicable to storage)",
                    quantity=Decimal(0),
                    unit="%",
                    unit_rate=Decimal(0),
                    amount=Decimal(0),
                    detail="storage is never discounted",
                )
            )
        payload = {
            "cloud_id": cloud_id,
            "account_id": account_id,
            "offering_id": offering_id,
            "tier": tier,
            "gib": str(gib),
            "pricing_version": self.version,
            "when": when,
        }
        quote_id = "q_" + hashlib.sha256(stable_json(payload).encode()).hexdigest()[:24]
        return PriceQuote(
            quote_id=quote_id,
            cloud_id=cloud_id,
            account_id=account_id,
            offering_id=offering_id,
            offering_name=offering_name,
            shape=None,
            currency=card.currency,
            rate_card_version=card.version,
            pricing_version=f"{self.version}:{card.version}:storage",
            effective_at=when,
            compute_hourly=Decimal(0),
            storage_hourly=amount,
            discount_hourly=discount,
            total_hourly=amount,
            status=PriceStatus.PRICED,
            line_items=lines,
            override_id=override.id if override else None,
            notes=["storage-only quote; compute is charged separately"],
        )

    def covers_offering(self, cloud_id: str, offering_id: str, when: datetime) -> bool:
        return self.binding_for(cloud_id, offering_id, when) is not None

    def current_rate_card(self, cloud_id: str, when: datetime) -> RateCard | None:
        """Latest applicable rate card referenced by this cloud's bindings.

        Used only for display estimates of storage that is not part of an
        offering shape (for example a detached data volume).
        """

        when = ensure_utc(when)
        versions = {
            binding.rate_card_version
            for binding in self.bindings
            if binding.cloud_id == cloud_id and binding.applies_at(when)
        }
        cards = [self.rate_card(version) for version in versions]
        if not cards:
            return None
        return max(cards, key=lambda card: card.effective_from)


def _latest(overrides: list[PriceOverride]) -> PriceOverride:
    return sorted(overrides, key=lambda item: (item.effective_from, item.version), reverse=True)[0]


@dataclass
class CatalogBuilder:
    """Small helper for operator import and tests."""

    rate_cards: list[RateCard] = field(default_factory=list)
    bindings: list[OfferingBinding] = field(default_factory=list)
    overrides: list[PriceOverride] = field(default_factory=list)
    version: str = "1"
    source_checksum: str | None = None

    def build(self) -> PricingCatalog:
        return PricingCatalog(
            rate_cards=tuple(self.rate_cards),
            bindings=tuple(self.bindings),
            overrides=tuple(self.overrides),
            version=self.version,
            source_checksum=self.source_checksum,
        )
