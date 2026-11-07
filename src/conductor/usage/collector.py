"""CloudStack usage collection with idempotent, gap-aware persistence.

The collector uses a separate, restricted administrative reader credential. It
never runs inside a customer request path and never writes through the execution
service. Overlapping windows are reconciled by usage record ID; gaps are
reported, never reconstructed from present state.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.cloudstack.client import CloudStackClient, CloudStackCredentials
from conductor.cloudstack.models import RawUsageRecord
from conductor.config import Settings, read_secret_file
from conductor.db.base import new_id, utcnow
from conductor.db.session import build_engine, build_session_factory
from conductor.db.usage import UsageRepository
from conductor.errors import ConfigurationError
from conductor.logging import configure_logging
from conductor.models.usage import UsageCollectionWindow, UsageGap, UsageRecord

logger = logging.getLogger(__name__)

# CloudStack usage type identifiers used by the standard usage server.
RUNNING_VM_USAGE = 1
ALLOCATED_VM_USAGE = 2
VOLUME_USAGE = 5
SNAPSHOT_USAGE = 8


class UsageCollector:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        client: CloudStackClient,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.client = client

    async def collect(self, start: datetime, end: datetime, *, window_id: str | None = None) -> UsageCollectionWindow:
        if end <= start:
            raise ValueError("end must be after start")
        window_id = window_id or new_id("usage")
        records = await self.client.list_usage_records(start, end)
        converted = [
            _record_to_usage(record, cloud_id=self.settings.cloud_id, window_id=window_id) for record in records
        ]
        async with self.session_factory() as session:
            repo = UsageRepository(session)
            previous = await repo.latest_window(self.settings.cloud_id)
            inserted = await repo.insert_records(converted)
            duplicates = max(0, len(converted) - inserted)
            gaps: list[UsageGap] = []
            if previous is not None and start > previous.ended_at:
                gaps.append(
                    UsageGap(
                        start_at=previous.ended_at,
                        end_at=start,
                        reason="collection window gap",
                    )
                )
            window = UsageCollectionWindow(
                window_id=window_id,
                cloud_id=self.settings.cloud_id,
                started_at=start,
                ended_at=end,
                status="provisional" if end > utcnow() - timedelta(hours=25) else "complete",
                records_seen=len(converted),
                records_stored=inserted,
                duplicates=duplicates,
                gaps=gaps,
                created_at=utcnow(),
            )
            await repo.create_window(window)
            await session.commit()
        logger.info(
            "usage window collected: seen=%s stored=%s duplicates=%s",
            len(converted),
            inserted,
            duplicates,
        )
        return window


def _record_to_usage(record: RawUsageRecord, *, cloud_id: str, window_id: str) -> UsageRecord:
    usage_type = int(record.usagetype or 0)
    quantity = record.usage if record.usage is not None else Decimal(0)
    resource_type = record.resourcetype or _resource_type(usage_type)
    return UsageRecord(
        record_id=record.usageid,
        cloud_id=cloud_id,
        account_id=record.accountid or "",
        zone_id=record.zoneid,
        resource_id=record.resourceid or record.virtualmachineid or record.usageid,
        resource_name=record.resourcename,
        resource_type=resource_type,
        usage_type=usage_type,
        description=record.description,
        offering_id=record.offeringid,
        start_at=record.startdate or datetime.now(tz=UTC),
        end_at=record.enddate or datetime.now(tz=UTC),
        quantity=quantity,
        unit=_unit(usage_type),
        offered_rate=None,
        collected_at=utcnow(),
        window_id=window_id,
    )


def _resource_type(usage_type: int) -> str:
    return {
        RUNNING_VM_USAGE: "instance-running",
        ALLOCATED_VM_USAGE: "instance-allocated",
        VOLUME_USAGE: "volume",
        SNAPSHOT_USAGE: "snapshot",
    }.get(usage_type, "other")


def _unit(usage_type: int) -> str:
    if usage_type in {RUNNING_VM_USAGE, ALLOCATED_VM_USAGE}:
        return "hours"
    if usage_type in {VOLUME_USAGE, SNAPSHOT_USAGE}:
        return "gib-hours"
    return "units"


def reader_credentials(settings: Settings) -> CloudStackCredentials:
    if not (
        settings.usage.reader_api_url_file
        and settings.usage.reader_api_key_file
        and settings.usage.reader_api_secret_file
    ):
        raise ConfigurationError("the usage reader credential files are not configured")
    return CloudStackCredentials(
        api_url=read_secret_file(settings.usage.reader_api_url_file, name="the usage reader API URL"),
        api_key=read_secret_file(settings.usage.reader_api_key_file, name="the usage reader API key"),
        api_secret=read_secret_file(settings.usage.reader_api_secret_file, name="the usage reader API secret"),
    )


async def collect_once(settings: Settings, session_factory, *, days: int | None = None):
    credentials = reader_credentials(settings)
    end = utcnow()
    start = end - timedelta(days=days or settings.usage.lookback_days)
    async with CloudStackClient(credentials, rate_per_second=4.0) as client:
        collector = UsageCollector(settings, session_factory, client)
        return await collector.collect(start, end)


async def _main_async() -> None:
    settings = Settings()
    configure_logging(settings.log_level, service="conductor-usage")
    if not settings.usage.enabled:
        raise ConfigurationError("usage collection is disabled")
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    try:
        while True:
            try:
                window = await collect_once(settings, session_factory)
                logger.info("usage collection completed: %s", window.window_id)
            except Exception:
                logger.exception("usage collection failed")
            await asyncio.sleep(settings.usage.collection_interval_minutes * 60)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":  # pragma: no cover
    main()
