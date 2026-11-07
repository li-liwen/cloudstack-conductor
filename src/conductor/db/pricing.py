"""Pricing catalogue persistence.

Historical versions are retained. A new import inserts new rate cards and
effective-dated bindings; closing an old binding is an explicit operator step,
and the catalogue validator rejects overlaps.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from conductor.db.models import (
    PriceBindingRow,
    PriceImportRow,
    PriceOverrideRow,
    PricingPublicationRow,
    RateCardRow,
)
from conductor.models.common import ensure_utc
from conductor.models.pricing import ComputeShape, OfferingBinding, PriceOverride, RateCard
from conductor.pricing.engine import PricingCatalog
from conductor.pricing.importer import ImportResult


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _card(row: RateCardRow) -> RateCard:
    return RateCard(
        version=row.version,
        currency=row.currency,
        effective_from=ensure_utc(row.effective_from),
        effective_to=ensure_utc(row.effective_to) if row.effective_to else None,
        cpu_hour=_decimal(row.cpu_hour),
        memory_gib_hour=_decimal(row.memory_gib_hour),
        gpu_hour=_decimal(row.gpu_hour),
        ssd_gib_hour=_decimal(row.ssd_gib_hour),
        hdd_gib_hour=_decimal(row.hdd_gib_hour),
        source_checksum=row.source_checksum,
        notes=row.notes,
    )


def _binding(row: PriceBindingRow) -> OfferingBinding:
    return OfferingBinding(
        cloud_id=row.cloud_id,
        offering_id=row.offering_id,
        offering_name=row.offering_name,
        shape=ComputeShape.model_validate(row.shape),
        rate_card_version=row.rate_card_version,
        currency=row.currency,
        effective_from=ensure_utc(row.effective_from),
        effective_to=ensure_utc(row.effective_to) if row.effective_to else None,
        mapping_version=row.mapping_version,
        source_reference=row.source_reference,
        notes=row.notes,
    )


def _override(row: PriceOverrideRow) -> PriceOverride:
    return PriceOverride(
        id=row.id,
        cloud_id=row.cloud_id,
        scope=cast(Any, row.scope),
        scope_id=row.scope_id,
        discount_percent=_decimal(row.discount_percent),
        effective_from=ensure_utc(row.effective_from),
        effective_to=ensure_utc(row.effective_to) if row.effective_to else None,
        version=row.version,
        note=row.note,
        created_by=row.created_by,
    )


class PricingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def load_catalog(self, cloud_id: str) -> PricingCatalog:
        cards_result = await self.session.execute(select(RateCardRow).where(RateCardRow.cloud_id == cloud_id))
        bindings_result = await self.session.execute(
            select(PriceBindingRow).where(PriceBindingRow.cloud_id == cloud_id)
        )
        overrides_result = await self.session.execute(
            select(PriceOverrideRow).where(PriceOverrideRow.cloud_id == cloud_id)
        )
        publication = await self.latest_publication(cloud_id)
        return PricingCatalog(
            rate_cards=tuple(_card(row) for row in cards_result.scalars().all()),
            bindings=tuple(_binding(row) for row in bindings_result.scalars().all()),
            overrides=tuple(_override(row) for row in overrides_result.scalars().all()),
            version=publication.version if publication else "live",
            source_checksum=publication.checksum if publication else None,
        )

    async def latest_publication(self, cloud_id: str) -> PricingPublicationRow | None:
        result = await self.session.execute(
            select(PricingPublicationRow)
            .where(PricingPublicationRow.cloud_id == cloud_id)
            .order_by(PricingPublicationRow.published_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def save_import(
        self,
        *,
        cloud_id: str,
        result: ImportResult,
        imported_by: str,
        catalog_version: str,
        effective_from: datetime | None = None,
    ) -> PriceImportRow:
        """Persist a validated import, then publish it as the active version."""

        if not result.ok:
            raise ValueError("cannot persist an import with errors")
        effective_from = effective_from or result.effective_from
        for card in result.rate_cards:
            exists = await self.session.get(RateCardRow, {"cloud_id": cloud_id, "version": card.version})
            if exists is None:
                self.session.add(
                    RateCardRow(
                        cloud_id=cloud_id,
                        version=card.version,
                        currency=card.currency,
                        effective_from=effective_from,
                        effective_to=card.effective_to,
                        cpu_hour=card.cpu_hour,
                        memory_gib_hour=card.memory_gib_hour,
                        gpu_hour=card.gpu_hour,
                        ssd_gib_hour=card.ssd_gib_hour,
                        hdd_gib_hour=card.hdd_gib_hour,
                        source_checksum=card.source_checksum,
                        notes=card.notes,
                    )
                )
        for binding in result.bindings:
            existing_binding = await self.session.execute(
                select(PriceBindingRow).where(
                    PriceBindingRow.cloud_id == cloud_id,
                    PriceBindingRow.offering_id == binding.offering_id,
                    PriceBindingRow.effective_from == effective_from,
                )
            )
            if existing_binding.scalar_one_or_none() is not None:
                continue
            self.session.add(
                PriceBindingRow(
                    cloud_id=cloud_id,
                    offering_id=binding.offering_id,
                    offering_name=binding.offering_name,
                    shape=binding.shape.model_dump(mode="json"),
                    rate_card_version=binding.rate_card_version,
                    currency=binding.currency,
                    effective_from=effective_from,
                    effective_to=binding.effective_to,
                    mapping_version=binding.mapping_version,
                    source_reference=binding.source_reference,
                    notes=binding.notes,
                )
            )
        import_row = PriceImportRow(
            cloud_id=cloud_id,
            checksum=result.checksum,
            mapping_version=result.mapping_version,
            row_count=len(result.tariffs),
            imported_by=imported_by,
            manifest={
                "sheet": result.sheet,
                "rate_cards": [card.version for card in result.rate_cards],
                "bindings": len(result.bindings),
                "tariffs": len(result.tariffs),
                "warnings": [issue.code for issue in result.warnings],
                "effective_from": effective_from.isoformat(),
            },
        )
        self.session.add(import_row)
        publication = PricingPublicationRow(
            version=catalog_version,
            cloud_id=cloud_id,
            published_by=imported_by,
            checksum=result.checksum,
            manifest={"import_checksum": result.checksum, "tariffs": len(result.tariffs)},
        )
        self.session.add(publication)
        await self.session.flush()
        return import_row

    async def close_open_bindings(self, cloud_id: str, offering_ids: list[str], *, effective_to: datetime) -> int:
        """Close open-ended bindings before publishing a new effective date."""

        if not offering_ids:
            return 0
        result = await self.session.execute(
            select(PriceBindingRow).where(
                PriceBindingRow.cloud_id == cloud_id,
                PriceBindingRow.offering_id.in_(offering_ids),
                PriceBindingRow.effective_to.is_(None),
            )
        )
        count = 0
        for row in result.scalars().all():
            if row.effective_from < effective_to:
                row.effective_to = effective_to
                count += 1
        if count:
            await self.session.flush()
        return count

    async def save_catalog(
        self,
        *,
        cloud_id: str,
        catalog: PricingCatalog,
        published_by: str,
        version: str,
    ) -> None:
        """Insert a complete synthetic/initial catalogue. Imports use save_import."""

        for card in catalog.rate_cards:
            if await self.session.get(RateCardRow, {"cloud_id": cloud_id, "version": card.version}) is None:
                self.session.add(
                    RateCardRow(
                        cloud_id=cloud_id,
                        version=card.version,
                        currency=card.currency,
                        effective_from=card.effective_from,
                        effective_to=card.effective_to,
                        cpu_hour=card.cpu_hour,
                        memory_gib_hour=card.memory_gib_hour,
                        gpu_hour=card.gpu_hour,
                        ssd_gib_hour=card.ssd_gib_hour,
                        hdd_gib_hour=card.hdd_gib_hour,
                        source_checksum=card.source_checksum,
                        notes=card.notes,
                    )
                )
        for binding in catalog.bindings:
            exists = await self.session.execute(
                select(PriceBindingRow).where(
                    PriceBindingRow.cloud_id == cloud_id,
                    PriceBindingRow.offering_id == binding.offering_id,
                    PriceBindingRow.effective_from == binding.effective_from,
                )
            )
            if exists.scalar_one_or_none() is None:
                self.session.add(
                    PriceBindingRow(
                        cloud_id=cloud_id,
                        offering_id=binding.offering_id,
                        offering_name=binding.offering_name,
                        shape=binding.shape.model_dump(mode="json"),
                        rate_card_version=binding.rate_card_version,
                        currency=binding.currency,
                        effective_from=binding.effective_from,
                        effective_to=binding.effective_to,
                        mapping_version=binding.mapping_version,
                        source_reference=binding.source_reference,
                        notes=binding.notes,
                    )
                )
        for override in catalog.overrides:
            if await self.session.get(PriceOverrideRow, override.id) is None:
                self.session.add(
                    PriceOverrideRow(
                        id=override.id,
                        cloud_id=cloud_id,
                        scope=override.scope,
                        scope_id=override.scope_id,
                        discount_percent=override.discount_percent,
                        effective_from=override.effective_from,
                        effective_to=override.effective_to,
                        version=override.version,
                        note=override.note,
                        created_by=override.created_by,
                    )
                )
        self.session.add(
            PricingPublicationRow(
                version=version,
                cloud_id=cloud_id,
                published_by=published_by,
                checksum=catalog.source_checksum,
                manifest={
                    "source": "initial",
                    "rate_cards": [card.version for card in catalog.rate_cards],
                },
            )
        )
        await self.session.flush()

    async def create_override(self, override: PriceOverride, *, created_by: str) -> None:
        self.session.add(
            PriceOverrideRow(
                id=override.id,
                cloud_id=override.cloud_id,
                scope=override.scope,
                scope_id=override.scope_id,
                discount_percent=override.discount_percent,
                effective_from=override.effective_from,
                effective_to=override.effective_to,
                version=override.version,
                note=override.note,
                created_by=created_by,
            )
        )
        await self.session.flush()

    async def list_cards(self, cloud_id: str) -> list[RateCardRow]:
        result = await self.session.execute(
            select(RateCardRow).where(RateCardRow.cloud_id == cloud_id).order_by(RateCardRow.effective_from.desc())
        )
        return list(result.scalars().all())

    async def list_bindings(self, cloud_id: str) -> list[PriceBindingRow]:
        result = await self.session.execute(
            select(PriceBindingRow).where(PriceBindingRow.cloud_id == cloud_id).order_by(PriceBindingRow.offering_name)
        )
        return list(result.scalars().all())

    async def list_overrides(self, cloud_id: str) -> list[PriceOverrideRow]:
        result = await self.session.execute(
            select(PriceOverrideRow)
            .where(PriceOverrideRow.cloud_id == cloud_id)
            .order_by(PriceOverrideRow.effective_from.desc())
        )
        return list(result.scalars().all())

    async def find_import(self, cloud_id: str, checksum: str) -> PriceImportRow | None:
        result = await self.session.execute(
            select(PriceImportRow)
            .where(PriceImportRow.cloud_id == cloud_id, PriceImportRow.checksum == checksum)
            .limit(1)
        )
        return result.scalar_one_or_none()
