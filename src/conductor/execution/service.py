"""Execution service operations: plans, approvals, quotes, resources, console.

This is the only service that decrypts per-user CloudStack credentials and
submits customer changes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.config import Settings
from conductor.db.base import new_id, utcnow
from conductor.db.identity import AuditRepository, CredentialRepository
from conductor.db.operations import OperationRepository, PlanRepository
from conductor.errors import (
    ApprovalExpired,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    PlanChanged,
)
from conductor.execution.cloud import (
    CloudAccess,
    PricingService,
    ResourceService,
    load_principal,
    offering_shape,
)
from conductor.execution.console import ConsoleBroker
from conductor.execution.planner import Planner
from conductor.execution.queue import OperationQueue
from conductor.execution.runner import OperationRunner
from conductor.models.console import ConsoleLaunch
from conductor.models.enums import (
    ApprovalRequirement,
    OperationState,
)
from conductor.models.operation import Operation
from conductor.models.plan import ActionPlan, Approval, PlanView, QuoteSummary
from conductor.models.pricing import PriceQuote
from conductor.policy.actions import QuoteParams, approval_requirement
from conductor.policy.authorization import ensure_account_context
from conductor.pricing.estimates import quote_summary
from conductor.security.crypto import Vault, constant_time_equals


class ExecutionService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        vault: Vault,
        queue: OperationQueue | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.vault = vault
        self.queue = queue
        self.runner = OperationRunner(settings, session_factory, vault)

    # -- plans -------------------------------------------------------------
    async def create_plan(
        self,
        *,
        subject: str,
        action: str,
        parameters: dict[str, Any],
        idempotency_key: str | None,
        request_id: str | None,
    ) -> ActionPlan:
        async with self.session_factory() as session:
            planner = Planner(session, self.settings, self.vault)
            plan = await planner.prepare(
                subject=subject,
                action=action,
                raw_params=parameters,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
            await session.commit()
            return plan

    async def list_plans(self, subject: str, *, limit: int = 50, offset: int = 0) -> list[ActionPlan]:
        async with self.session_factory() as session:
            plans = PlanRepository(session)
            return await plans.list_for_principal(subject, limit=limit, offset=offset)

    async def get_plan(self, plan_id: str, subject: str) -> PlanView:
        async with self.session_factory() as session:
            plans = PlanRepository(session)
            plan = await plans.get(plan_id)
            if plan is None or (plan.principal_subject != subject):
                raise NotFoundError("the plan was not found")
            approval = await plans.get_approval(plan_id)
            return PlanView(
                plan=plan,
                approval=approval,
                quote_summary=self._summary(plan.quote),
                can_approve=(
                    plan.state == "awaiting_approval"
                    and plan.expires_at > utcnow()
                    and approval_requirement(plan.action, plan.parameters) is not ApprovalRequirement.NONE
                ),
                approval_ttl_seconds=self.settings.limits.approval_ttl_minutes * 60,
            )

    async def approve_plan(
        self,
        *,
        plan_id: str,
        subject: str,
        digest: str,
        note: str | None,
        client_fingerprint: str | None = None,
        request_id: str | None = None,
    ) -> tuple[ActionPlan, Operation]:
        now = utcnow()
        async with self.session_factory() as session:
            plans = PlanRepository(session)
            operations = OperationRepository(session)
            audit = AuditRepository(session)
            plan = await plans.get_for_update(plan_id)
            if plan is None or plan.principal_subject != subject:
                raise NotFoundError("the plan was not found")
            if plan.state != "awaiting_approval":
                raise ConflictError("the plan is not awaiting approval")
            if plan.expires_at <= now:
                raise ApprovalExpired("the plan has expired; prepare it again")
            if not constant_time_equals(plan.digest, digest):
                raise PlanChanged("the approval digest does not match the plan")
            existing = await operations.latest_for_plan(plan_id)
            if existing is not None:
                if existing.state not in {
                    OperationState.FAILED,
                    OperationState.CANCELLED,
                    OperationState.NEEDS_RECONCILIATION,
                }:
                    raise ConflictError("an operation for this plan already exists")
            if await plans.get_approval(plan_id) is not None:
                raise ConflictError("this plan has already been approved")
            approval_expiry = min(
                now + timedelta(minutes=self.settings.limits.approval_ttl_minutes),
                plan.expires_at,
            )
            approval = Approval(
                approval_id=new_id("apr"),
                plan_id=plan.plan_id,
                digest=plan.digest,
                approved_by=subject,
                approved_at=now,
                expires_at=approval_expiry,
                method="browser_session",
                note=note,
                client_fingerprint=client_fingerprint,
            )
            await plans.save_approval(approval)
            await plans.set_state(plan_id, "approved")
            plan = plan.model_copy(update={"state": "approved"})
            operation = Operation(
                operation_id=new_id("op"),
                plan_id=plan.plan_id,
                principal_subject=plan.principal_subject,
                tenant_key=plan.tenant_key,
                cloud_id=plan.cloud_id,
                account_id=plan.account_id,
                project_id=plan.project_id,
                action=plan.action.value,
                state=OperationState.QUEUED,
                created_at=now,
                updated_at=now,
                approval_id=approval.approval_id,
                plan_digest=plan.digest,
                idempotency_key=approval.approval_id,
                request_id=request_id,
            )
            await operations.create(operation)
            await operations.append_event(
                operation.operation_id,
                OperationState.QUEUED,
                "已批准，等待执行。",
                "Approved and queued for execution.",
            )
            await audit.append(
                actor_subject=subject,
                action="plan.approved",
                target_type="plan",
                target_id=plan.plan_id,
                outcome="success",
                request_id=request_id,
                detail={
                    "approval_id": approval.approval_id,
                    "operation_id": operation.operation_id,
                },
            )
            await session.commit()
        if self.queue is not None:
            try:
                await self.queue.enqueue(operation.operation_id)
            except Exception:
                # The operation remains queued and is re-enqueued on service
                # restart; approval is never lost.
                import logging

                logging.getLogger(__name__).exception("could not enqueue operation %s", operation.operation_id)
        return plan, operation

    async def reject_plan(self, *, plan_id: str, subject: str, request_id: str | None = None) -> ActionPlan:
        async with self.session_factory() as session:
            plans = PlanRepository(session)
            plan = await plans.get_for_update(plan_id)
            if plan is None or plan.principal_subject != subject:
                raise NotFoundError("the plan was not found")
            if plan.state != "awaiting_approval":
                raise ConflictError("the plan is not awaiting approval")
            await plans.set_state(plan_id, "rejected")
            await AuditRepository(session).append(
                actor_subject=subject,
                action="plan.rejected",
                target_type="plan",
                target_id=plan_id,
                outcome="success",
                request_id=request_id,
            )
            await session.commit()
            return plan.model_copy(update={"state": "rejected"})

    # -- quotes and resources ---------------------------------------------
    async def quote(self, *, subject: str, params: dict[str, Any]) -> PriceQuote:
        typed = QuoteParams.model_validate(params)
        async with self.session_factory() as session:
            principal = await load_principal(session, self.settings, subject)
            account_id = typed.account_id or principal.cloudstack_account_id
            ensure_account_context(principal, account_id, typed.project_id)
            access = CloudAccess(session, self.settings, self.vault, principal)
            resources = ResourceService(access)
            offering = await resources.find_offering(typed.offering_id)
            if offering is None:
                raise NotFoundError("the service offering was not found")
            quote = await PricingService(session).quote_offering(
                principal,
                offering_id=offering.id,
                offering_name=offering.name,
                shape_fallback=offering_shape(offering),
                account_id=account_id,
                extra_ssd_gib=typed.disk_size_gib or Decimal(0),
            )
            return quote

    async def list_resources(
        self,
        *,
        subject: str,
        kind: str = "all",
        account_id: str | None = None,
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            principal = await load_principal(session, self.settings, subject)
            ensure_account_context(principal, account_id)
            access = CloudAccess(session, self.settings, self.vault, principal)
            resources = ResourceService(access)
            items: dict[str, list[dict[str, Any]]] = {}
            if kind in {"instance", "all"}:
                items["instances"] = [_instance_view(vm) for vm in await resources.list_instances(account_id)]
            if kind in {"volume", "all"}:
                items["volumes"] = [_volume_view(volume) for volume in await resources.list_volumes(account_id)]
            if kind in {"network", "all"}:
                items["networks"] = [_network_view(network) for network in await resources.list_networks(account_id)]
            if kind in {"offering", "all"}:
                items["offerings"] = [_offering_view(offering) for offering in await resources.list_offerings()]
            if kind in {"template", "all"}:
                items["templates"] = [_template_view(template) for template in await resources.list_templates()]
            return {"items": items, "refreshed_at": utcnow().isoformat()}

    async def cloud_health(self, subject: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            principal = await load_principal(session, self.settings, subject)
            access = CloudAccess(session, self.settings, self.vault, principal)
            async with access.client() as client:
                capabilities = await client.get_capabilities()
                zones = await client.list_zones()
            return {
                "reachable": True,
                "cloudstack_version": capabilities.cloudstackversion,
                "zones": len(zones),
                "security_groups_enabled": capabilities.securitygroupsenabled,
            }

    async def credentials_status(self, subject: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            principal = await load_principal(session, self.settings, subject)
            row = await CredentialRepository(session).get(principal.cloud_id, principal.cloudstack_user_id)
            return {
                "provisioned": row is not None,
                "disabled": bool(row.disabled) if row is not None else False,
                "last_used_at": row.last_used_at.isoformat() if row and row.last_used_at else None,
            }

    async def forecast_current_usage(self, subject: str) -> dict[str, Any]:
        """Current-state cost forecast.

        This is not historical usage. It assumes the present resources continue,
        labels itself provisional, and never claims to be a bill.
        """

        async with self.session_factory() as session:
            principal = await load_principal(session, self.settings, subject)
            access = CloudAccess(session, self.settings, self.vault, principal)
            resources = ResourceService(access)
            instances = await resources.list_instances()
            volumes = await resources.list_volumes()
            offerings = {item.id: item for item in await resources.list_offerings()}
            pricing = PricingService(session)
            compute_hourly = Decimal(0)
            storage_hourly = Decimal(0)
            unpriced = 0
            for instance in instances:
                if not instance.running or not instance.serviceofferingid:
                    continue
                offering = offerings.get(instance.serviceofferingid)
                if offering is None:
                    unpriced += 1
                    continue
                quote = await pricing.quote_offering(
                    principal,
                    offering_id=offering.id,
                    offering_name=offering.name,
                    shape_fallback=offering_shape(offering),
                )
                if quote.priced:
                    compute_hourly += quote.compute_hourly
                else:
                    unpriced += 1
            catalog = await pricing.catalog(principal.cloud_id)
            card = catalog.current_rate_card(principal.cloud_id, utcnow())
            for volume in volumes:
                gib = volume.size_gib
                tier = "hdd" if "hdd" in str(volume.storage or "").lower() else "ssd"
                if card is None:
                    unpriced += 1
                    continue
                rate = card.hdd_gib_hour if tier == "hdd" else card.ssd_gib_hour
                storage_hourly += (gib * rate).quantize(Decimal("0.0001"))
            total = (compute_hourly + storage_hourly).quantize(Decimal("0.0001"))
            now = utcnow()
            month = quote_hours_month(now)
            return {
                "currency": "CNY",
                "compute_hourly": format(compute_hourly, "f"),
                "storage_hourly": format(storage_hourly, "f"),
                "total_hourly": format(total, "f"),
                "month_reference": format((total * month).quantize(Decimal("0.01")), "f"),
                "completeness": "partial" if unpriced else "provisional",
                "unpriced_resources": unpriced,
                "generated_at": now.isoformat(),
                "assumptions": [
                    "按当前资源状态持续估算，不是历史用量账单。",
                    "计算费用仅在运行中的云主机上产生；存储费用持续到磁盘删除。",
                    "近期用量记录未经汇总时，结果视为临时数据。",
                ],
                "disclaimer_zh": "费用预估，仅供参考，以最终账单为准。",
            }

    # -- operations --------------------------------------------------------
    async def plan_operation(self, plan_id: str, subject: str) -> Operation:
        async with self.session_factory() as session:
            plans = PlanRepository(session)
            plan = await plans.get(plan_id)
            if plan is None or plan.principal_subject != subject:
                raise NotFoundError("the plan was not found")
            operation = await OperationRepository(session).latest_for_plan(plan_id)
            if operation is None:
                raise NotFoundError("no operation exists for this plan yet")
            return operation

    async def list_operations(self, subject: str, *, limit: int = 50, offset: int = 0) -> list[Operation]:
        async with self.session_factory() as session:
            return await OperationRepository(session).list_for_principal(subject, limit=limit, offset=offset)

    async def get_operation(self, operation_id: str, subject: str) -> Operation:
        async with self.session_factory() as session:
            operation = await OperationRepository(session).get(operation_id)
            if operation is None or operation.principal_subject != subject:
                raise NotFoundError("the operation was not found")
            return operation

    async def operation_events(self, operation_id: str, subject: str, *, after: int = 0):
        async with self.session_factory() as session:
            operations = OperationRepository(session)
            operation = await operations.get(operation_id)
            if operation is None or operation.principal_subject != subject:
                raise NotFoundError("the operation was not found")
            return await operations.events(operation_id, after_id=after)

    async def cancel_operation(self, operation_id: str, subject: str) -> Operation:
        return await self.runner.cancel(operation_id, subject=subject)

    async def reconcile_operation(self, operation_id: str, subject: str) -> Operation:
        return await self.runner.reconcile(operation_id, subject=subject)

    async def recover_queued(self) -> int:
        """Re-enqueue operations that were queued when a process stopped."""

        if self.queue is None:
            return 0
        count = 0
        async with self.session_factory() as session:
            operations = await OperationRepository(session).list_all(limit=200)
            queued = [item for item in operations if item.state is OperationState.QUEUED]
        for operation in queued:
            try:
                await self.queue.enqueue(operation.operation_id)
                count += 1
            except Exception:
                import logging

                logging.getLogger(__name__).exception("could not recover operation %s", operation.operation_id)
        return count

    # -- console -----------------------------------------------------------
    async def console_lease(self, *, subject: str, vm_id: str) -> ConsoleLaunch:
        async with self.session_factory() as session:
            broker = ConsoleBroker(session, self.settings, self.vault)
            launch = await broker.lease(subject=subject, vm_id=vm_id)
            await session.commit()
            return launch

    async def console_exchange(self, *, subject: str, lease_id: str, token: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            broker = ConsoleBroker(session, self.settings, self.vault)
            console_session = await broker.exchange(lease_id, token, subject=subject)
            await session.commit()
            return console_session.model_dump(mode="json")

    async def console_endpoint(self, session_id: str, subject: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            broker = ConsoleBroker(session, self.settings, self.vault)
            lease, endpoint = await broker.endpoint_for_session(session_id)
            if lease.principal_subject != subject:
                raise AuthorizationError("the console session belongs to another principal")
            return {"endpoint": endpoint, "vm_id": lease.vm_id}

    # -- helpers -----------------------------------------------------------
    def _summary(self, quote: PriceQuote | None) -> QuoteSummary | None:
        if quote is None:
            return None
        summary = quote_summary(quote, utcnow())
        return QuoteSummary(
            currency=str(summary["currency"]),
            hourly=Decimal(str(summary["hourly"])),
            monthly=Decimal(str(summary["monthly"])),
            half_year=Decimal(str(summary["half_year"])),
            status=str(summary["status"]),
            partial=bool(summary["partial"]),
        )


def quote_hours_month(now: datetime) -> Decimal:
    from conductor.pricing.periods import hours_between, period_containing

    period = period_containing(now, kind="month")
    return hours_between(period.starts_at, period.ends_at)


def _instance_view(vm: Any) -> dict[str, Any]:
    return {
        "id": vm.id,
        "name": vm.name,
        "state": vm.state,
        "ip_addresses": vm.ip_addresses,
        "offering_id": vm.serviceofferingid,
        "offering_name": vm.serviceofferingname,
        "template_id": vm.templateid,
        "template_name": vm.templatename,
        "zone_id": vm.zoneid,
        "zone_name": vm.zonename,
        "account_id": vm.accountid,
        "account": vm.account,
        "created": vm.created.isoformat() if vm.created else None,
        "running": vm.running,
    }


def _volume_view(volume: Any) -> dict[str, Any]:
    return {
        "id": volume.id,
        "name": volume.name,
        "type": volume.type,
        "size_gib": format(volume.size_gib, "f"),
        "state": volume.state,
        "attached_to": volume.virtualmachineid,
        "vm_name": volume.vmname,
        "disk_offering_id": volume.diskofferingid,
        "disk_offering_name": volume.diskofferingname,
        "zone_id": volume.zoneid,
        "account_id": volume.accountid,
        "created": volume.created.isoformat() if volume.created else None,
    }


def _network_view(network: Any) -> dict[str, Any]:
    return {
        "id": network.id,
        "name": network.name,
        "type": network.type,
        "state": network.state,
        "cidr": network.cidr,
        "gateway": network.gateway,
        "vlan": network.vlan,
        "zone_id": network.zoneid,
        "account_id": network.accountid,
        "is_default": network.isdefault,
    }


def _offering_view(offering: Any) -> dict[str, Any]:
    return {
        "id": offering.id,
        "name": offering.name,
        "cpu_count": offering.cpunumber,
        "cpu_speed_mhz": offering.cpuspeed,
        "memory_gib": format(offering.memory_gib, "f"),
        "gpu_count": offering.gpunumber,
    }


def _template_view(template: Any) -> dict[str, Any]:
    return {
        "id": template.id,
        "name": template.name,
        "os_type": template.ostypename,
        "format": template.format,
        "hypervisor": template.hypervisor,
        "ready": template.isready,
        "public": template.ispublic,
    }
