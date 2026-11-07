"""Plan construction.

The planner validates capabilities, checks ownership, prices the change, and
builds an immutable, digest-bound plan. It never accepts a price, approval, or
resource owner from the model or a client string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from conductor.config import Settings
from conductor.db.base import new_id
from conductor.db.identity import AuditRepository
from conductor.db.operations import PlanRepository
from conductor.errors import (
    CapabilityUnsupported,
    ConflictError,
    NotFoundError,
    ValidationFailure,
)
from conductor.execution.cloud import (
    CloudAccess,
    PricingService,
    ResourceService,
    load_principal,
    offering_shape,
)
from conductor.models.common import utcnow
from conductor.models.enums import ActionType, GuestTaskType
from conductor.models.plan import ActionPlan, PlanEffect, PlanResource
from conductor.models.pricing import PriceQuote
from conductor.models.principal import Principal
from conductor.policy.actions import (
    AttachVolumeParams,
    CreateInstanceParams,
    CreateNetworkParams,
    CreateSnapshotParams,
    CreateVolumeParams,
    DeleteInstanceParams,
    DeleteVolumeParams,
    GuestTaskParams,
    NetworkRef,
    RebootInstanceParams,
    RegisterSshKeyParams,
    ReinstallInstanceParams,
    ResizeInstanceParams,
    ResizeVolumeParams,
    ResourceRef,
    RestoreInstanceParams,
    RestoreSnapshotParams,
    StopInstanceParams,
    UpdateFirewallParams,
    VolumeRef,
    approval_requirement,
    get_spec,
)
from conductor.policy.authorization import (
    ensure_account_context,
    ensure_action_allowed,
    ensure_expunge_allowed,
)
from conductor.pricing.estimates import quote_summary
from conductor.security.crypto import Vault


@dataclass
class Draft:
    quote: PriceQuote | None = None
    resources: list[PlanResource] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    effects: list[PlanEffect] = field(default_factory=list)
    downtime_seconds: int | None = None
    data_effects: list[str] = field(default_factory=list)
    explanation_zh: str = ""
    explanation_en: str = ""


class Planner:
    def __init__(self, session: AsyncSession, settings: Settings, vault: Vault) -> None:
        self.session = session
        self.settings = settings
        self.vault = vault
        self.plans = PlanRepository(session)
        self.audit = AuditRepository(session)

    async def prepare(
        self,
        *,
        subject: str,
        action: ActionType | str,
        raw_params: dict[str, Any],
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> ActionPlan:
        principal = await load_principal(self.session, self.settings, subject)
        spec = get_spec(action)
        if not spec.mutating:
            raise ValidationFailure("this action does not require a plan")
        if idempotency_key:
            existing = await self.plans.find_by_idempotency(subject, idempotency_key)
            if existing is not None:
                if existing.action.value != spec.action.value or existing.parameters != raw_params:
                    raise ConflictError("the idempotency key was already used for a different request")
                return existing
        typed = spec.params_model.model_validate(raw_params)
        data = typed.model_dump(mode="json")
        account_id = data.get("account_id") or principal.cloudstack_account_id
        project_id = data.get("project_id")
        ensure_account_context(principal, account_id, project_id)
        ensure_action_allowed(principal, spec.action)
        if spec.action is ActionType.GUEST_TASK and not self.settings.features.guest_tasks_enabled:
            raise CapabilityUnsupported("managed guest tasks are disabled in this deployment")
        access = CloudAccess(self.session, self.settings, self.vault, principal)
        draft = await self._dispatch(access, spec.action, typed, account_id, principal)
        now = utcnow()
        plan = ActionPlan(
            plan_id=new_id("plan"),
            principal_subject=principal.subject,
            tenant_key=principal.tenant_key,
            cloud_id=principal.cloud_id,
            account_id=account_id,
            project_id=project_id,
            action=spec.action,
            parameters=data,
            resources=draft.resources,
            quote=draft.quote,
            pricing_version=draft.quote.pricing_version if draft.quote else None,
            preconditions=draft.preconditions,
            effects=draft.effects,
            downtime_seconds=draft.downtime_seconds,
            data_effects=draft.data_effects,
            approval_requirement=approval_requirement(spec.action, typed),
            state="awaiting_approval",
            created_at=now,
            expires_at=now + timedelta(minutes=self.settings.limits.plan_ttl_minutes),
            explanation_zh=draft.explanation_zh,
            explanation_en=draft.explanation_en,
            request_id=request_id,
            idempotency_key=idempotency_key,
        ).with_digest()
        if not plan.digest:
            raise ValidationFailure("the plan digest could not be computed")
        await self.plans.create(plan)
        await self.audit.append(
            actor_subject=subject,
            action="plan.created",
            target_type="plan",
            target_id=plan.plan_id,
            outcome="success",
            request_id=request_id,
            detail={"action": plan.action.value, "digest": plan.digest},
        )
        return plan

    async def _dispatch(
        self,
        access: CloudAccess,
        action: ActionType,
        params: object,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        handler = getattr(self, f"_plan_{action.value}", None)
        if handler is None:
            raise ValidationFailure("this action is not supported")
        return await handler(access, params, account_id, principal)

    # -- helpers -----------------------------------------------------------
    def _pricing(self) -> PricingService:
        return PricingService(self.session)

    async def _quote_for_offering(
        self,
        access: CloudAccess,
        principal: Principal,
        *,
        offering_id: str,
        offering_name: str,
        account_id: str,
        fallback_shape=None,
        extra_ssd_gib: Decimal = Decimal(0),
        extra_hdd_gib: Decimal = Decimal(0),
    ) -> PriceQuote:
        return await self._pricing().quote_offering(
            principal,
            offering_id=offering_id,
            offering_name=offering_name,
            shape_fallback=fallback_shape,
            account_id=account_id,
            extra_ssd_gib=extra_ssd_gib,
            extra_hdd_gib=extra_hdd_gib,
        )

    async def _quote_current_offering(
        self, access: CloudAccess, principal: Principal, vm, account_id: str
    ) -> tuple[PriceQuote | None, str]:
        offering = None
        if vm.serviceofferingid:
            offering = await ResourceService(access).find_offering(vm.serviceofferingid)
        if offering is None:
            return None, vm.serviceofferingname or "unknown offering"
        quote = await self._quote_for_offering(
            access,
            principal,
            offering_id=offering.id,
            offering_name=offering.name,
            account_id=account_id,
            fallback_shape=offering_shape(offering),
        )
        return quote, offering.name

    def _require_priced(self, quote: PriceQuote, *, reason: str) -> None:
        if not quote.priced:
            raise CapabilityUnsupported(f"{reason}: the service offering has no valid price")

    def _cost_effect(
        self,
        kind: str,
        *,
        quote: PriceQuote | None,
        delta: Decimal | None,
        zh: str,
        en: str,
        resource_id: str | None = None,
    ) -> PlanEffect:
        return PlanEffect(
            kind=kind,  # type: ignore[arg-type]
            summary_zh=zh,
            summary_en=en,
            resource_id=resource_id,
            cost_delta_hourly=delta,
        )

    def _quote_text(self, quote: PriceQuote) -> tuple[str, str]:
        if not quote.priced:
            return (
                "该规格尚未定价，费用不完整。",
                "This shape is unpriced; the estimate is incomplete.",
            )
        summary = quote_summary(quote, utcnow())
        return (
            f"每小时 {quote.total_hourly} {quote.currency}，月参考 {summary['monthly']} {quote.currency}。",
            f"{quote.total_hourly} {quote.currency}/hour; monthly reference {summary['monthly']} {quote.currency}.",
        )

    async def _vm(self, access: CloudAccess, vm_id: str):
        return await ResourceService(access).get_vm(vm_id)

    async def _volume(self, access: CloudAccess, volume_id: str):
        return await ResourceService(access).get_volume(volume_id)

    async def _disk_offerings(self, access: CloudAccess) -> list[dict[str, Any]]:
        async with access.client() as client:
            return await client.list_all("listDiskOfferings", "diskoffering")

    # -- action handlers ---------------------------------------------------
    async def _plan_create_instance(
        self,
        access: CloudAccess,
        params: CreateInstanceParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        resources = ResourceService(access)
        offering = await resources.find_offering(params.offering_id)
        if offering is None:
            raise NotFoundError("the service offering was not found")
        template = await resources.find_template(params.template_id)
        if template is None or not template.isready:
            raise NotFoundError("the template is not available")
        zone = await resources.find_zone(params.zone_id)
        if zone is None:
            raise NotFoundError("the zone was not found")
        networks = []
        for network_id in params.network_ids:
            network = await resources.find_network(network_id, account_id)
            if network is None:
                raise NotFoundError("a selected network was not found")
            networks.append(network)
        quote = await self._quote_for_offering(
            access,
            principal,
            offering_id=offering.id,
            offering_name=offering.name,
            account_id=account_id,
            fallback_shape=offering_shape(offering),
        )
        if not quote.priced:
            raise CapabilityUnsupported(
                "this service offering has not been priced; provisioning through the agent is blocked"
            )
        if params.disk_size_gib is not None:
            if params.disk_size_gib < (quote.shape.total_storage_gib if quote.shape else 0):
                raise ValidationFailure("the requested root disk is smaller than the offering's disk")
            if params.disk_offering_id is None:
                raise ValidationFailure("a disk offering is required when changing the root disk size")
            offerings = await self._disk_offerings(access)
            disk = next((item for item in offerings if item.get("id") == params.disk_offering_id), None)
            if disk is None:
                raise NotFoundError("the disk offering was not found")
            tier = "hdd" if "hdd" in str(disk.get("storagetype", "")).lower() else "ssd"
            included = quote.shape.total_storage_gib if quote.shape else Decimal(0)
            extra = params.disk_size_gib - included
            quote = await self._quote_for_offering(
                access,
                principal,
                offering_id=offering.id,
                offering_name=offering.name,
                account_id=account_id,
                fallback_shape=offering_shape(offering),
                extra_ssd_gib=extra if tier == "ssd" else Decimal(0),
                extra_hdd_gib=extra if tier == "hdd" else Decimal(0),
            )
            self._require_priced(quote, reason="the custom root disk has no price")
        zh, en = self._quote_text(quote)
        effects = [
            self._cost_effect(
                "create",
                quote=quote,
                delta=quote.total_hourly,
                zh=f"将创建云主机 {params.name}；开始运行后按小时计费。",
                en=f"Creates instance {params.name}; charges accrue while it runs.",
            ),
            self._cost_effect(
                "cost_change",
                quote=quote,
                delta=quote.total_hourly,
                zh=f"{zh}",
                en=f"{en}",
            ),
        ]
        if params.start:
            effects.append(
                PlanEffect(
                    kind="start",
                    summary_zh="创建后立即启动。",
                    summary_en="Starts immediately after creation.",
                )
            )
        else:
            effects.append(
                PlanEffect(
                    kind="stop",
                    summary_zh="创建后保持关机；计算费用不产生，存储费用持续。",
                    summary_en="Remains stopped; no compute charge, storage continues.",
                )
            )
        return Draft(
            quote=quote,
            resources=[
                PlanResource(kind="offering", resource_id=offering.id, name=offering.name),
                PlanResource(kind="template", resource_id=template.id, name=template.name),
                *[PlanResource(kind="network", resource_id=net.id, name=net.name) for net in networks],
            ],
            preconditions=[
                f"offering:{offering.id}",
                f"template:{template.id}:ready",
                f"zone:{zone.id}",
                *[f"network:{net.id}" for net in networks],
            ],
            effects=effects,
            downtime_seconds=0,
            explanation_zh=(
                f"将按 {offering.name}（{offering.cpunumber} vCPU / {offering.memory_gib} GiB）"
                f"创建云主机 {params.name}。{zh}"
            ),
            explanation_en=(
                f"Creates {params.name} with {offering.name} ({offering.cpunumber} vCPU / "
                f"{offering.memory_gib} GiB). {en}"
            ),
        )

    async def _plan_start_instance(
        self, access: CloudAccess, params: ResourceRef, account_id: str, principal: Principal
    ) -> Draft:
        vm = await self._vm(access, params.instance_id)
        if not vm.stopped:
            raise CapabilityUnsupported("the instance is not stopped")
        quote, offering_name = await self._quote_current_offering(access, principal, vm, account_id)
        delta = quote.compute_hourly if quote and quote.priced else None
        zh, en = self._quote_text(quote) if quote else ("该规格尚未定价。", "This shape is unpriced.")
        return Draft(
            quote=quote,
            resources=[PlanResource(kind="instance", resource_id=vm.id, name=vm.name)],
            preconditions=[f"instance:{vm.id}:stopped"],
            effects=[
                PlanEffect(
                    kind="start",
                    summary_zh="将启动云主机；计算费用恢复计费。",
                    summary_en="Starts the instance; compute charging resumes.",
                    resource_id=vm.id,
                    cost_delta_hourly=delta,
                )
            ],
            downtime_seconds=0,
            explanation_zh=f"启动 {vm.name}（{offering_name}）。{zh}",
            explanation_en=f"Starts {vm.name} ({offering_name}). {en}",
        )

    async def _plan_stop_instance(
        self, access: CloudAccess, params: StopInstanceParams, account_id: str, principal: Principal
    ) -> Draft:
        vm = await self._vm(access, params.instance_id)
        if not vm.running:
            raise CapabilityUnsupported("the instance is not running")
        quote, offering_name = await self._quote_current_offering(access, principal, vm, account_id)
        savings = -quote.compute_hourly if quote and quote.priced else None
        return Draft(
            quote=quote,
            resources=[PlanResource(kind="instance", resource_id=vm.id, name=vm.name)],
            preconditions=[f"instance:{vm.id}:running"],
            effects=[
                PlanEffect(
                    kind="stop",
                    summary_zh="将停止云主机；计算费用停止累计，存储费用继续。",
                    summary_en="Stops the instance; compute charges stop, storage continues.",
                    resource_id=vm.id,
                    cost_delta_hourly=savings,
                )
            ],
            downtime_seconds=0,
            explanation_zh=f"停止 {vm.name}（{offering_name}）后服务不可用，直到再次启动。",
            explanation_en=f"{vm.name} ({offering_name}) becomes unavailable until started again.",
        )

    async def _plan_reboot_instance(
        self,
        access: CloudAccess,
        params: RebootInstanceParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        vm = await self._vm(access, params.instance_id)
        if not vm.running:
            raise CapabilityUnsupported("the instance is not running")
        return Draft(
            resources=[PlanResource(kind="instance", resource_id=vm.id, name=vm.name)],
            preconditions=[f"instance:{vm.id}:running"],
            effects=[
                PlanEffect(
                    kind="reboot",
                    summary_zh="将重启云主机，预计短暂中断。",
                    summary_en="Reboots the instance with a brief interruption.",
                    resource_id=vm.id,
                )
            ],
            downtime_seconds=120,
            explanation_zh=f"重启 {vm.name}，预计约 2 分钟不可用。",
            explanation_en=f"Reboots {vm.name}; expect about 2 minutes of downtime.",
        )

    async def _plan_resize_instance(
        self,
        access: CloudAccess,
        params: ResizeInstanceParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        resources = ResourceService(access)
        vm = await self._vm(access, params.instance_id)
        target = await resources.find_offering(params.offering_id)
        if target is None:
            raise NotFoundError("the target service offering was not found")
        if vm.serviceofferingid == target.id:
            raise ValidationFailure("the instance already uses this offering")
        current_quote, current_name = await self._quote_current_offering(access, principal, vm, account_id)
        target_quote = await self._quote_for_offering(
            access,
            principal,
            offering_id=target.id,
            offering_name=target.name,
            account_id=account_id,
            fallback_shape=offering_shape(target),
        )
        self._require_priced(target_quote, reason="the target offering")
        delta = target_quote.total_hourly - (
            current_quote.total_hourly if current_quote and current_quote.priced else Decimal(0)
        )
        running = vm.running
        downtime = 300 if running else 0
        effect_zh = f"将从 {current_name} 调整为 {target.name}。" + (
            "运行中的云主机将依次停机、调整、启动。" if running else ""
        )
        effect_en = f"Changes {current_name} to {target.name}." + (
            " The running instance is stopped, changed, and started again." if running else ""
        )
        return Draft(
            quote=target_quote,
            resources=[
                PlanResource(kind="instance", resource_id=vm.id, name=vm.name),
                PlanResource(kind="offering", resource_id=target.id, name=target.name),
            ],
            preconditions=[f"instance:{vm.id}:exists", f"offering:{target.id}"],
            effects=[
                PlanEffect(
                    kind="resize",
                    summary_zh=effect_zh,
                    summary_en=effect_en,
                    resource_id=vm.id,
                    cost_delta_hourly=delta,
                )
            ],
            downtime_seconds=downtime,
            explanation_zh=(
                f"{effect_zh} 新规格每小时 {target_quote.total_hourly} {target_quote.currency}，"
                f"较当前每小时变化 {delta}。"
            ),
            explanation_en=(
                f"{effect_en} The new rate is {target_quote.total_hourly} {target_quote.currency}/hour, "
                f"a delta of {delta} per hour."
            ),
        )

    async def _plan_delete_instance(
        self,
        access: CloudAccess,
        params: DeleteInstanceParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        ensure_expunge_allowed(params, principal)
        vm = await self._vm(access, params.instance_id)
        async with access.client() as client:
            volumes = await client.list_volumes(account_id=access.account_param(account_id))
        attached = [volume for volume in volumes if volume.virtualmachineid == vm.id]
        volume_ids = {volume.id for volume in attached}
        unknown = set(params.delete_volume_ids) - volume_ids
        if unknown:
            raise ValidationFailure("a volume to delete is not attached to this instance")
        data_effects = []
        for volume in attached:
            deleted = volume.id in params.delete_volume_ids
            action = "删除" if deleted else "保留"
            action_en = "deleted" if deleted else "retained"
            data_effects.append(
                f"{volume.name or volume.id} ({volume.type or 'volume'}, {volume.size_gib} GiB): {action} / {action_en}"
            )
        effects = [
            PlanEffect(
                kind="delete",
                summary_zh="将销毁云主机" + ("并彻底清除" if params.expunge else "（保留可恢复状态）") + "。",
                summary_en="Destroys the instance" + (" and expunges it." if params.expunge else "."),
                resource_id=vm.id,
            ),
            PlanEffect(
                kind="data_loss",
                summary_zh="云主机系统盘数据将丢失；未明确删除的数据盘按 CloudStack 规则保留。",
                summary_en=(
                    "The root disk data is lost; data volumes not explicitly deleted are "
                    "retained according to CloudStack."
                ),
                resource_id=vm.id,
                data_at_risk=[volume.id for volume in attached],
            ),
        ]
        return Draft(
            resources=[
                PlanResource(kind="instance", resource_id=vm.id, name=vm.name),
                *[PlanResource(kind="volume", resource_id=volume.id, name=volume.name) for volume in attached],
            ],
            preconditions=[f"instance:{vm.id}:exists"],
            effects=effects,
            downtime_seconds=0,
            data_effects=data_effects,
            explanation_zh=(
                f"删除 {vm.name}。"
                + ("执行彻底清除，无法恢复。" if params.expunge else "可在 CloudStack 中恢复，直到清理。")
            ),
            explanation_en=(
                f"Deletes {vm.name}. "
                + ("The expunge is irreversible." if params.expunge else "Recoverable until expunged.")
            ),
        )

    async def _plan_restore_instance(
        self,
        access: CloudAccess,
        params: RestoreInstanceParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        vm = await self._vm(access, params.instance_id)
        return Draft(
            resources=[PlanResource(kind="instance", resource_id=vm.id, name=vm.name)],
            preconditions=[f"instance:{vm.id}:exists"],
            effects=[
                PlanEffect(
                    kind="data_loss",
                    summary_zh="将从模板恢复云主机，现有系统盘数据被替换。",
                    summary_en="Restores the instance from a template, replacing root disk data.",
                    resource_id=vm.id,
                )
            ],
            downtime_seconds=300,
            data_effects=["系统盘数据将被替换 / root disk data will be replaced"],
            explanation_zh=f"恢复 {vm.name} 到模板状态，原系统盘内容将丢失。",
            explanation_en=f"Restores {vm.name} to the template; current root disk contents are lost.",
        )

    async def _plan_reinstall_instance(
        self,
        access: CloudAccess,
        params: ReinstallInstanceParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        vm = await self._vm(access, params.instance_id)
        return Draft(
            resources=[PlanResource(kind="instance", resource_id=vm.id, name=vm.name)],
            preconditions=[f"instance:{vm.id}:exists"],
            effects=[
                PlanEffect(
                    kind="data_loss",
                    summary_zh="将重装云主机，现有系统盘数据被替换。",
                    summary_en="Reinstalls the instance, replacing root disk data.",
                    resource_id=vm.id,
                )
            ],
            downtime_seconds=600,
            data_effects=["系统盘数据将被重装 / root disk data will be reinstalled"],
            explanation_zh=f"重装 {vm.name}，系统盘内容将丢失。",
            explanation_en=f"Reinstalls {vm.name}; root disk contents are lost.",
        )

    async def _plan_create_volume(
        self,
        access: CloudAccess,
        params: CreateVolumeParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        resources = ResourceService(access)
        zone = await resources.find_zone(params.zone_id)
        if zone is None:
            raise NotFoundError("the zone was not found")
        tier = "ssd"
        disk_name = "default disk offering"
        if params.disk_offering_id:
            offerings = await self._disk_offerings(access)
            disk = next((item for item in offerings if item.get("id") == params.disk_offering_id), None)
            if disk is None:
                raise NotFoundError("the disk offering was not found")
            disk_name = str(disk.get("name") or params.disk_offering_id)
            tier = "hdd" if "hdd" in str(disk.get("storagetype", "")).lower() else "ssd"
        quote = await self._pricing().quote_storage(
            principal,
            offering_id=params.disk_offering_id or params.zone_id,
            offering_name=disk_name,
            tier=tier,
            gib=params.size_gib,
            account_id=account_id,
        )
        self._require_priced(quote, reason="this disk offering")
        return Draft(
            quote=quote,
            resources=[PlanResource(kind="offering", resource_id=params.disk_offering_id or "", name=disk_name)],
            preconditions=[f"zone:{zone.id}", f"size_gib:{params.size_gib}"],
            effects=[
                PlanEffect(
                    kind="create",
                    summary_zh=f"将创建 {params.size_gib} GiB 数据盘；存储费用持续到删除。",
                    summary_en=(f"Creates a {params.size_gib} GiB volume; storage charges continue until deletion."),
                    cost_delta_hourly=quote.total_hourly,
                )
            ],
            downtime_seconds=0,
            explanation_zh=f"创建数据盘 {params.name}，每小时存储费用 {quote.total_hourly} {quote.currency}。",
            explanation_en=(f"Creates volume {params.name} at {quote.total_hourly} {quote.currency}/hour for storage."),
        )

    async def _plan_attach_volume(
        self,
        access: CloudAccess,
        params: AttachVolumeParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        volume = await self._volume(access, params.volume_id)
        vm = await self._vm(access, params.instance_id)
        if volume.attached:
            raise CapabilityUnsupported("the volume is already attached")
        return Draft(
            resources=[
                PlanResource(kind="volume", resource_id=volume.id, name=volume.name),
                PlanResource(kind="instance", resource_id=vm.id, name=vm.name),
            ],
            preconditions=[f"volume:{volume.id}:detached", f"instance:{vm.id}:exists"],
            effects=[
                PlanEffect(
                    kind="resize",
                    summary_zh="将数据盘挂载到云主机，不改变费用。",
                    summary_en="Attaches the volume to the instance; no price change.",
                    resource_id=volume.id,
                )
            ],
            downtime_seconds=0,
            explanation_zh=f"将 {volume.name or volume.id} 挂载到 {vm.name}。",
            explanation_en=f"Attaches {volume.name or volume.id} to {vm.name}.",
        )

    async def _plan_detach_volume(
        self, access: CloudAccess, params: VolumeRef, account_id: str, principal: Principal
    ) -> Draft:
        volume = await self._volume(access, params.volume_id)
        if not volume.attached:
            raise CapabilityUnsupported("the volume is not attached")
        return Draft(
            resources=[PlanResource(kind="volume", resource_id=volume.id, name=volume.name)],
            preconditions=[f"volume:{volume.id}:attached"],
            effects=[
                PlanEffect(
                    kind="stop",
                    summary_zh="将卸载数据盘；存储费用继续，挂载点上的数据访问会中断。",
                    summary_en="Detaches the volume; storage charges continue and access is interrupted.",
                    resource_id=volume.id,
                )
            ],
            downtime_seconds=60,
            explanation_zh=f"卸载 {volume.name or volume.id}，请先在客户机内停止使用该磁盘。",
            explanation_en=f"Detaches {volume.name or volume.id}; stop using the disk inside the guest first.",
        )

    async def _plan_resize_volume(
        self,
        access: CloudAccess,
        params: ResizeVolumeParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        volume = await self._volume(access, params.volume_id)
        current = volume.size_gib
        if params.size_gib <= current:
            raise CapabilityUnsupported("volume shrinking is not supported; only expansion is allowed")
        tier = "hdd" if "hdd" in str(volume.storage or "").lower() else "ssd"
        delta_gib = params.size_gib - current
        quote = await self._pricing().quote_storage(
            principal,
            offering_id=volume.diskofferingid or volume.id,
            offering_name=volume.diskofferingname or "volume",
            tier=tier,
            gib=delta_gib,
            account_id=account_id,
        )
        self._require_priced(quote, reason="this volume")
        return Draft(
            quote=quote,
            resources=[PlanResource(kind="volume", resource_id=volume.id, name=volume.name)],
            preconditions=[f"volume:{volume.id}:size={current}"],
            effects=[
                PlanEffect(
                    kind="resize",
                    summary_zh=f"将数据盘从 {current} GiB 扩容到 {params.size_gib} GiB；"
                    "文件系统扩容需要单独的受控客户机任务。",
                    summary_en=(
                        f"Expands the volume from {current} GiB to {params.size_gib} GiB; "
                        "filesystem expansion needs a separate managed guest task."
                    ),
                    resource_id=volume.id,
                    cost_delta_hourly=quote.total_hourly,
                )
            ],
            downtime_seconds=0,
            explanation_zh=(
                f"扩容 {volume.name or volume.id} 至 {params.size_gib} GiB，"
                f"新增存储每小时 {quote.total_hourly} {quote.currency}。"
            ),
            explanation_en=(
                f"Expands {volume.name or volume.id} to {params.size_gib} GiB for "
                f"{quote.total_hourly} {quote.currency}/hour of additional storage."
            ),
        )

    async def _plan_delete_volume(
        self, access: CloudAccess, params: DeleteVolumeParams, account_id: str, principal: Principal
    ) -> Draft:
        volume = await self._volume(access, params.volume_id)
        data_effects = [f"{volume.name or volume.id}: 删除后数据不可恢复 / data is not recoverable"]
        return Draft(
            resources=[PlanResource(kind="volume", resource_id=volume.id, name=volume.name)],
            preconditions=[f"volume:{volume.id}:exists"],
            effects=[
                PlanEffect(
                    kind="data_loss",
                    summary_zh="将删除数据盘，盘上数据全部丢失；存储费用停止。",
                    summary_en="Deletes the volume and all its data; storage charges stop.",
                    resource_id=volume.id,
                    data_at_risk=[volume.id],
                )
            ],
            downtime_seconds=0,
            data_effects=data_effects,
            explanation_zh=f"删除 {volume.name or volume.id}，数据不可恢复。",
            explanation_en=f"Deletes {volume.name or volume.id}; the data is unrecoverable.",
        )

    async def _plan_create_snapshot(
        self,
        access: CloudAccess,
        params: CreateSnapshotParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        volume = await self._volume(access, params.volume_id)
        if volume.virtualmachineid:
            vm = await self._vm(access, volume.virtualmachineid)
            if vm.running:
                raise CapabilityUnsupported("live snapshots are disabled; stop the instance or detach the volume first")
        return Draft(
            resources=[
                PlanResource(kind="volume", resource_id=volume.id, name=volume.name),
                PlanResource(kind="snapshot", resource_id="pending", name="new snapshot"),
            ],
            preconditions=[f"volume:{volume.id}:exists"],
            effects=[
                PlanEffect(
                    kind="create",
                    summary_zh="将创建卷快照；快照占用存储，费用按存储计。",
                    summary_en="Creates a volume snapshot; snapshot storage is charged as storage.",
                    resource_id=volume.id,
                )
            ],
            downtime_seconds=0,
            explanation_zh=f"为 {volume.name or volume.id} 创建快照。",
            explanation_en=f"Creates a snapshot of {volume.name or volume.id}.",
        )

    async def _plan_restore_snapshot(
        self,
        access: CloudAccess,
        params: RestoreSnapshotParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        return Draft(
            resources=[PlanResource(kind="snapshot", resource_id=params.snapshot_id, name="snapshot")],
            preconditions=[f"snapshot:{params.snapshot_id}:exists"],
            effects=[
                PlanEffect(
                    kind="create",
                    summary_zh="将从快照创建新的数据盘，不改写现有磁盘。",
                    summary_en="Creates a new volume from the snapshot; existing disks are not overwritten.",
                    resource_id=params.snapshot_id,
                )
            ],
            downtime_seconds=0,
            explanation_zh="从快照创建新数据盘，原磁盘保持不变。",
            explanation_en="Creates a new volume from the snapshot and leaves the original unchanged.",
        )

    async def _plan_create_network(
        self,
        access: CloudAccess,
        params: CreateNetworkParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        resources = ResourceService(access)
        zone = await resources.find_zone(params.zone_id)
        if zone is None:
            raise NotFoundError("the zone was not found")
        return Draft(
            resources=[PlanResource(kind="network", resource_id=params.network_offering_id, name=params.name)],
            preconditions=[f"zone:{zone.id}", f"cidr:{params.cidr}"],
            effects=[
                PlanEffect(
                    kind="network_exposure",
                    summary_zh=f"将创建私有隔离网络 {params.name}（{params.cidr}）。",
                    summary_en=f"Creates isolated network {params.name} ({params.cidr}).",
                )
            ],
            downtime_seconds=0,
            explanation_zh=f"创建隔离网络 {params.name}，地址段 {params.cidr}。",
            explanation_en=f"Creates isolated network {params.name} with CIDR {params.cidr}.",
        )

    async def _plan_delete_network(
        self, access: CloudAccess, params: NetworkRef, account_id: str, principal: Principal
    ) -> Draft:
        async with access.client() as client:
            networks = await client.list_networks(account_id=access.account_param(account_id))
        network = next((item for item in networks if item.id == params.network_id), None)
        if network is None:
            raise NotFoundError("the network was not found")
        return Draft(
            resources=[PlanResource(kind="network", resource_id=network.id, name=network.name)],
            preconditions=[f"network:{network.id}:exists"],
            effects=[
                PlanEffect(
                    kind="data_loss",
                    summary_zh="将删除网络及其配置；连接在该网络上的资源会受影响。",
                    summary_en="Deletes the network and its configuration; attached resources are affected.",
                    resource_id=network.id,
                )
            ],
            downtime_seconds=0,
            data_effects=["网络配置将被删除 / network configuration will be deleted"],
            explanation_zh=f"删除网络 {network.name}。",
            explanation_en=f"Deletes network {network.name}.",
        )

    async def _plan_update_firewall(
        self,
        access: CloudAccess,
        params: UpdateFirewallParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        async with access.client() as client:
            networks = await client.list_networks(account_id=access.account_param(account_id))
        network = next((item for item in networks if item.id == params.network_id), None)
        if network is None:
            raise NotFoundError("the network was not found")
        summaries = []
        for rule in params.add:
            summaries.append(
                f"{rule.protocol.upper()} {rule.start_port}-{rule.end_port} from " + ", ".join(rule.source_cidrs)
            )
        summary = "; ".join(summaries) or "no additions"
        return Draft(
            resources=[PlanResource(kind="network", resource_id=network.id, name=network.name)],
            preconditions=[f"network:{network.id}:exists"],
            effects=[
                PlanEffect(
                    kind="network_exposure",
                    summary_zh=f"将修改网络 {network.name} 的防火墙规则：{summary}。",
                    summary_en=f"Changes firewall rules on {network.name}: {summary}.",
                    resource_id=network.id,
                )
            ],
            downtime_seconds=0,
            explanation_zh="防火墙变更仅按上述端口和来源地址执行，不开放其他流量。",
            explanation_en="Only the disclosed ports and source ranges are changed.",
        )

    async def _plan_register_ssh_key(
        self,
        access: CloudAccess,
        params: RegisterSshKeyParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        return Draft(
            preconditions=[f"keypair_name:{params.name}"],
            effects=[
                PlanEffect(
                    kind="account",
                    summary_zh=f"将在账户中登记 SSH 公钥 {params.name}。",
                    summary_en=f"Registers SSH public key {params.name} in the account.",
                )
            ],
            downtime_seconds=0,
            explanation_zh=f"登记公钥 {params.name}；私钥不会上传或保存。",
            explanation_en=f"Registers public key {params.name}; no private key is uploaded or stored.",
        )

    async def _plan_guest_task(
        self,
        access: CloudAccess,
        params: GuestTaskParams,
        account_id: str,
        principal: Principal,
    ) -> Draft:
        vm = await self._vm(access, params.instance_id)
        if params.task is GuestTaskType.INSPECT_FILESYSTEM:
            downtime = 0
        else:
            downtime = 120
        return Draft(
            resources=[PlanResource(kind="instance", resource_id=vm.id, name=vm.name)],
            preconditions=[f"instance:{vm.id}:managed-template", f"guest_task:{params.task.value}"],
            effects=[
                PlanEffect(
                    kind="guest_change",
                    summary_zh=f"将通过受控 Ansible 任务在 {vm.name} 上执行 {params.task.value}。",
                    summary_en=f"Runs managed task {params.task.value} on {vm.name}.",
                    resource_id=vm.id,
                )
            ],
            downtime_seconds=downtime,
            data_effects=["仅执行已审核的剧本，不支持任意 shell / reviewed playbooks only"],
            explanation_zh="客户机任务使用审核过的剧本，默认先检查/演练。",
            explanation_en="Guest tasks use reviewed playbooks and default to inspection/dry-run.",
        )
