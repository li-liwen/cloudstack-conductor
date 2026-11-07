"""Usage and forecast estimates.

Running and allocated usage are separated. Costs come from the same pricing
catalogue used for quotes; records with no price make the total explicitly
partial. A forecast states the assumption that the current resource state
continues and uses actual calendar hours.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from conductor.config import Settings
from conductor.db.pricing import PricingRepository
from conductor.db.usage import UsageRepository
from conductor.models.common import ensure_utc, utcnow
from conductor.models.enums import UsageCompleteness
from conductor.models.pricing import PriceQuote
from conductor.models.usage import (
    BillingPeriod,
    ResourceUsageSummary,
    UsageEstimate,
)
from conductor.pricing.engine import money4
from conductor.pricing.periods import billable_hours, hours_between
from conductor.usage.collector import (
    ALLOCATED_VM_USAGE,
    RUNNING_VM_USAGE,
    SNAPSHOT_USAGE,
    VOLUME_USAGE,
)


class UsageEstimator:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.usage = UsageRepository(session)
        self.pricing = PricingRepository(session)

    async def estimate(
        self,
        *,
        account_id: str,
        period: BillingPeriod,
        mode: str = "full",
        now: datetime | None = None,
        locale: str = "zh-CN",
    ) -> UsageEstimate:
        now = ensure_utc(now) if now else utcnow()
        start = ensure_utc(period.starts_at)
        end = ensure_utc(period.ends_at)
        if mode == "remaining":
            start = max(start, now)
        records = await self.usage.records_for_account(self.settings.cloud_id, account_id, start, end)
        catalog = await self.pricing.load_catalog(self.settings.cloud_id)
        rate_card = catalog.current_rate_card(self.settings.cloud_id, now)
        resources: dict[str, ResourceUsageSummary] = {}
        compute_total = Decimal(0)
        storage_total = Decimal(0)
        unpriced = False
        for record in records:
            quantity = Decimal(str(record.quantity))
            summary = resources.get(record.resource_id)
            if summary is None:
                summary = ResourceUsageSummary(
                    resource_id=record.resource_id,
                    resource_name=record.resource_name,
                    resource_type=record.resource_type,
                    first_seen=ensure_utc(record.start_at),
                    last_seen=ensure_utc(record.end_at),
                )
                resources[record.resource_id] = summary
            summary.last_seen = max(summary.last_seen or ensure_utc(record.end_at), ensure_utc(record.end_at))
            if record.usage_type == RUNNING_VM_USAGE:
                summary.running_hours += quantity
                if record.offering_id:
                    quote = catalog.quote(
                        cloud_id=self.settings.cloud_id,
                        account_id=account_id,
                        offering_id=record.offering_id,
                        when=now,
                    )
                    if quote.priced:
                        compute_total += quote.compute_hourly * quantity
                    else:
                        unpriced = True
                else:
                    unpriced = True
            elif record.usage_type == ALLOCATED_VM_USAGE:
                summary.allocated_hours += quantity
            elif record.usage_type in {VOLUME_USAGE, SNAPSHOT_USAGE}:
                summary.storage_gib_hours += quantity
                if rate_card is None:
                    unpriced = True
                else:
                    # Usage records do not carry the storage tier; use the SSD
                    # rate and mark the estimate partial when no tier is known.
                    storage_total += rate_card.ssd_gib_hour * quantity
            else:
                unpriced = True
        windows = await self.usage.windows(self.settings.cloud_id, limit=100)
        gaps = [
            gap
            for window in windows
            for gap in window.gaps
            if ensure_utc(gap.start_at) < end and ensure_utc(gap.end_at) > start
        ]
        completeness = UsageCompleteness.PROVISIONAL
        if period.ends_at <= now:
            completeness = UsageCompleteness.COMPLETE if not unpriced else UsageCompleteness.PARTIAL
        if gaps:
            completeness = UsageCompleteness.PARTIAL
        compute = money4(compute_total)
        storage = money4(storage_total)
        total = money4(compute + storage)
        notes_zh = ["按当前资源状态与已汇总用量估算，不代表账单。"]
        notes_en = ["Estimated from collected usage and the current resource state; not a bill."]
        if unpriced:
            notes_zh.append("部分资源缺少价格，合计不完整。")
            notes_en.append("Some resources have no price; the total is incomplete.")
        if gap := gaps:
            notes_zh.append(f"存在 {len(gap)} 个采集缺口。")
            notes_en.append(f"{len(gap)} collection gaps exist.")
        return UsageEstimate(
            account_id=account_id,
            cloud_id=self.settings.cloud_id,
            period_start=start,
            period_end=end,
            resources=sorted(resources.values(), key=lambda item: item.resource_id),
            compute=compute,
            storage=storage,
            total=total,
            completeness=completeness,
            provisional=completeness is not UsageCompleteness.COMPLETE,
            pricing_version=catalog.version,
            generated_at=now,
            gaps=gaps,
            notes_zh=notes_zh,
            notes_en=notes_en,
        )

    async def forecast(
        self,
        *,
        account_id: str,
        period: BillingPeriod,
        now: datetime | None = None,
    ) -> UsageEstimate:
        """Accrued usage plus remaining hours at the observed average rate."""

        now = ensure_utc(now) if now else utcnow()
        accrued = await self.estimate(account_id=account_id, period=period, mode="remaining", now=now)
        remaining_hours = billable_hours(period, now, mode="remaining")
        elapsed_hours = billable_hours(period, now, mode="full") - remaining_hours
        if elapsed_hours <= 0 or accrued.total == 0:
            remaining_cost = Decimal(0)
        else:
            hourly = accrued.total / elapsed_hours
            remaining_cost = money4(hourly * remaining_hours)
        total = money4(accrued.compute + accrued.storage + remaining_cost)
        return accrued.model_copy(
            update={
                "total": total,
                "notes_zh": [
                    *accrued.notes_zh,
                    "预测假设当前资源状态持续到本周期结束。",
                ],
                "notes_en": [
                    *accrued.notes_en,
                    "The forecast assumes the current resource state continues to the end of the period.",
                ],
            }
        )


def estimate_from_quote(quote: PriceQuote, period: BillingPeriod, now: datetime) -> Decimal:
    hours = hours_between(period.starts_at, period.ends_at)
    return money4(quote.total_hourly * hours)
