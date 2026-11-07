"""CloudStack usage-record persistence with idempotent inserts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from conductor.db.models import UsageRecordRow, UsageWindowRow
from conductor.models.common import ensure_utc
from conductor.models.usage import UsageCollectionWindow, UsageGap, UsageRecord


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def dialect(self) -> str:
        return self.session.get_bind().dialect.name

    async def insert_records(self, records: list[UsageRecord]) -> int:
        if not records:
            return 0
        values = [
            {
                "record_id": record.record_id,
                "cloud_id": record.cloud_id,
                "account_id": record.account_id,
                "zone_id": record.zone_id,
                "resource_id": record.resource_id,
                "resource_name": record.resource_name,
                "resource_type": record.resource_type,
                "usage_type": record.usage_type,
                "description": record.description,
                "offering_id": record.offering_id,
                "start_at": record.start_at,
                "end_at": record.end_at,
                "quantity": record.quantity,
                "unit": record.unit,
                "offered_rate": record.offered_rate,
                "collected_at": record.collected_at,
                "window_id": record.window_id,
            }
            for record in records
        ]
        insert = pg_insert if self.dialect == "postgresql" else sqlite_insert
        statement = (
            insert(UsageRecordRow)
            .values(values)
            .on_conflict_do_nothing(index_elements=["record_id"])
            .returning(UsageRecordRow.record_id)
        )
        result = await self.session.execute(statement)
        inserted = len(list(result.scalars().all()))
        await self.session.flush()
        return inserted

    async def records_for_account(
        self,
        cloud_id: str,
        account_id: str,
        start: datetime,
        end: datetime,
    ) -> list[UsageRecordRow]:
        result = await self.session.execute(
            select(UsageRecordRow)
            .where(
                UsageRecordRow.cloud_id == cloud_id,
                UsageRecordRow.account_id == account_id,
                UsageRecordRow.start_at < ensure_utc(end),
                UsageRecordRow.end_at > ensure_utc(start),
            )
            .order_by(UsageRecordRow.start_at)
        )
        return list(result.scalars().all())

    async def records_for_resource(
        self, cloud_id: str, resource_id: str, start: datetime, end: datetime
    ) -> list[UsageRecordRow]:
        result = await self.session.execute(
            select(UsageRecordRow)
            .where(
                UsageRecordRow.cloud_id == cloud_id,
                UsageRecordRow.resource_id == resource_id,
                UsageRecordRow.start_at < ensure_utc(end),
                UsageRecordRow.end_at > ensure_utc(start),
            )
            .order_by(UsageRecordRow.start_at)
        )
        return list(result.scalars().all())

    async def create_window(self, window: UsageCollectionWindow) -> UsageCollectionWindow:
        row = UsageWindowRow(
            window_id=window.window_id,
            cloud_id=window.cloud_id,
            started_at=window.started_at,
            ended_at=window.ended_at,
            status=window.status,
            records_seen=window.records_seen,
            records_stored=window.records_stored,
            duplicates=window.duplicates,
            gaps=[gap.model_dump(mode="json") for gap in window.gaps],
            created_at=window.created_at,
        )
        self.session.add(row)
        await self.session.flush()
        return window

    async def windows(self, cloud_id: str, *, limit: int = 50) -> list[UsageCollectionWindow]:
        result = await self.session.execute(
            select(UsageWindowRow)
            .where(UsageWindowRow.cloud_id == cloud_id)
            .order_by(UsageWindowRow.started_at.desc())
            .limit(limit)
        )
        windows = []
        for row in result.scalars().all():
            windows.append(
                UsageCollectionWindow(
                    window_id=row.window_id,
                    cloud_id=row.cloud_id,
                    started_at=ensure_utc(row.started_at),
                    ended_at=ensure_utc(row.ended_at),
                    status=row.status,
                    records_seen=row.records_seen,
                    records_stored=row.records_stored,
                    duplicates=row.duplicates,
                    gaps=[UsageGap.model_validate(item) for item in (row.gaps or [])],
                    created_at=ensure_utc(row.created_at),
                )
            )
        return windows

    async def latest_window(self, cloud_id: str) -> UsageCollectionWindow | None:
        windows = await self.windows(cloud_id, limit=1)
        return windows[0] if windows else None

    async def total_quantity(self, cloud_id: str, account_id: str, start: datetime, end: datetime) -> Decimal:
        rows = await self.records_for_account(cloud_id, account_id, start, end)
        return sum((Decimal(str(row.quantity)) for row in rows), Decimal(0))
