"""Durable operation execution.

Execution re-checks the approval digest and the live CloudStack state before
each mutation. A lost response marks the operation ``needs_reconciliation``;
creates are never blindly retried.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.cloudstack.errors import CloudStackJobTimeout
from conductor.config import Settings
from conductor.db.base import utcnow
from conductor.db.models import OperationRow
from conductor.db.operations import OperationRepository, PlanRepository
from conductor.errors import (
    ApprovalExpired,
    AuthorizationError,
    CapabilityUnsupported,
    CloudStackError,
    ConflictError,
    NotFoundError,
    PlanChanged,
    ReconciliationRequired,
    ValidationFailure,
)
from conductor.execution.cloud import CloudAccess, load_principal
from conductor.models.enums import OperationState, StepState
from conductor.models.operation import Operation, OperationStep
from conductor.models.plan import ActionPlan, Approval
from conductor.models.principal import Principal
from conductor.policy.actions import (
    get_spec,
)
from conductor.policy.authorization import ensure_approval
from conductor.security.crypto import Vault

logger = logging.getLogger(__name__)


class _Cancelled(Exception):
    """The operator requested cancellation before the next submitted step."""


class OperationRunner:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        vault: Vault,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.vault = vault

    async def run(self, operation_id: str) -> None:
        async with self.session_factory() as session:
            op_repo = OperationRepository(session)
            plan_repo = PlanRepository(session)
            operation = await op_repo.get_for_update(operation_id)
            if operation is None:
                logger.info("operation %s not found", operation_id)
                return
            if operation.state is not OperationState.QUEUED:
                return
            plan = await plan_repo.get(operation.plan_id)
            if plan is None:
                await self._fail(session, op_repo, operation, "plan_missing", "计划不存在")
                return
            approval = await plan_repo.get_approval(plan.plan_id)
            try:
                ensure_approval(
                    plan,
                    approval,
                    utcnow(),
                    principal_subject=operation.principal_subject,
                )
                if plan.state != "approved":
                    raise AuthorizationError("the plan is not approved")
            except (ApprovalExpired, PlanChanged, AuthorizationError) as exc:
                await self._fail(
                    session,
                    op_repo,
                    operation,
                    getattr(exc, "code", "approval_invalid"),
                    "执行前复核未通过，请重新准备并审批。 / Pre-execution revalidation failed.",
                )
                return
            principal = await load_principal(session, self.settings, operation.principal_subject)
            await op_repo.transition(
                operation_id,
                expected={OperationState.QUEUED},
                new_state=OperationState.RUNNING,
                message_zh="开始执行，正在重新核对云上状态。",
                message_en="Execution started; revalidating live cloud state.",
            )
            await session.commit()

        await self._execute(operation_id, principal, plan, approval)

    async def _execute(
        self,
        operation_id: str,
        principal: Principal,
        plan: ActionPlan,
        approval: Approval | None,
    ) -> None:
        async with self.session_factory() as session:
            op_repo = OperationRepository(session)
            plan_repo = PlanRepository(session)
            operation = await op_repo.get(operation_id)
            if operation is None:
                return
            access = CloudAccess(session, self.settings, self.vault, principal)
            params_model = get_spec(plan.action).params_model
            try:
                params = params_model.model_validate(plan.parameters)
            except ValueError:
                await self._fail(session, op_repo, operation, "invalid_plan", "计划参数无效。")
                return
            try:
                async with access.client() as client:
                    await self._dispatch(session, op_repo, operation, plan, params, access, client)
            except _Cancelled:
                await self._finish(
                    session,
                    op_repo,
                    operation,
                    OperationState.CANCELLED,
                    "已取消；已提交到云端的操作不会被回滚。",
                    "Cancelled; cloud-side changes already accepted are not undone.",
                )
            except ReconciliationRequired as exc:
                await self._reconcile_required(session, op_repo, operation, exc)
            except (ApprovalExpired, PlanChanged) as exc:
                await self._fail(session, op_repo, operation, exc.code, "审批已失效或计划已变更，请重新准备。")
            except CloudStackJobTimeout as exc:
                await self._reconcile_required(session, op_repo, operation, exc)
            except AuthorizationError as exc:
                await self._fail(session, op_repo, operation, exc.code, "权限复核未通过。")
            except (CapabilityUnsupported, ValidationFailure, NotFoundError, ConflictError) as exc:
                await self._fail(
                    session,
                    op_repo,
                    operation,
                    exc.code,
                    "云上状态已变化或请求不受支持，操作未执行。",
                )
            except CloudStackError as exc:
                await self._fail(
                    session,
                    op_repo,
                    operation,
                    exc.code,
                    "云平台拒绝了请求；请联系管理员查看私有日志。",
                )
            except Exception:
                logger.exception("operation %s failed unexpectedly", operation_id)
                await self._fail(
                    session,
                    op_repo,
                    operation,
                    "internal_error",
                    "执行时发生内部错误；操作已停止，请人工核对云上状态。",
                )
            else:
                resource_ids = operation.resource_ids
                await self._finish(
                    session,
                    op_repo,
                    operation,
                    OperationState.SUCCEEDED,
                    "执行完成，云上状态已核对。",
                    "Completed; the cloud state was verified.",
                    result={"resource_ids": resource_ids},
                )
                plan_row = await plan_repo.get(plan.plan_id)
                if plan_row is not None:
                    await plan_repo.set_state(plan.plan_id, "executed")
                await session.commit()

    # -- dispatch ----------------------------------------------------------
    async def _dispatch(
        self,
        session: AsyncSession,
        op_repo: OperationRepository,
        operation: Operation,
        plan: ActionPlan,
        params: Any,
        access: CloudAccess,
        client: Any,
    ) -> None:
        handler = getattr(self, f"_run_{plan.action.value}", None)
        if handler is None:
            raise CapabilityUnsupported("this operation is not supported")
        await self._step(
            session,
            op_repo,
            operation,
            "verify",
            "重新核对云上前置条件。",
            "Rechecking cloud preconditions.",
        )
        await handler(session, op_repo, operation, plan, params, access, client)

    async def _check_cancel(self, session: AsyncSession, operation: Operation) -> None:
        value = await session.scalar(
            select(OperationRow.cancel_requested_at).where(OperationRow.operation_id == operation.operation_id)
        )
        if value is not None:
            raise _Cancelled()

    # -- handlers ----------------------------------------------------------
    async def _run_create_instance(self, session, op_repo, op, plan, params, access, client):
        existing = await client.list_virtual_machines(account_id=access.account_param(plan.account_id))
        if any(vm.name == params.name for vm in existing):
            raise ReconciliationRequired("an instance with the requested name already exists; not creating a duplicate")
        await self._note(session, op_repo, op, "submitting", "提交创建请求。", "Submitting create request.")
        vm = await client.deploy_virtual_machine(
            service_offering_id=params.offering_id,
            template_id=params.template_id,
            zone_id=params.zone_id,
            network_ids=params.network_ids,
            name=params.name,
            start=params.start,
            disk_size_gib=params.disk_size_gib,
            disk_offering_id=params.disk_offering_id,
            keypair=params.ssh_key_name,
            group=params.group,
            account_id=access.account_param(plan.account_id),
            project_id=plan.project_id,
        )
        op.resource_ids = [vm.id]
        op.cloudstack_job_id = client.last_job_id
        await self._note(
            session,
            op_repo,
            op,
            "monitoring",
            f"云主机已创建：{vm.name or vm.id}（{vm.state}）。",
            f"Instance created: {vm.name or vm.id} ({vm.state}).",
        )
        if params.start and not vm.running:
            vm = await client.start_virtual_machine(vm.id)
            op.cloudstack_job_id = client.last_job_id
        op.result = {"instance_id": vm.id, "name": vm.name, "state": vm.state}

    async def _run_start_instance(self, session, op_repo, op, plan, params, access, client):
        vm = await client.get_virtual_machine(params.instance_id)
        if not vm.stopped:
            raise ConflictError("the instance is no longer stopped")
        await self._check_cancel(session, op)
        vm = await client.start_virtual_machine(vm.id)
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [vm.id]
        op.result = {"instance_id": vm.id, "state": vm.state}

    async def _run_stop_instance(self, session, op_repo, op, plan, params, access, client):
        vm = await client.get_virtual_machine(params.instance_id)
        if not vm.running:
            raise ConflictError("the instance is no longer running")
        await self._check_cancel(session, op)
        vm = await client.stop_virtual_machine(vm.id, force=params.force)
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [vm.id]
        op.result = {"instance_id": vm.id, "state": vm.state}

    async def _run_reboot_instance(self, session, op_repo, op, plan, params, access, client):
        vm = await client.get_virtual_machine(params.instance_id)
        if not vm.running:
            raise ConflictError("the instance is no longer running")
        await self._check_cancel(session, op)
        vm = await client.reboot_virtual_machine(vm.id)
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [vm.id]
        op.result = {"instance_id": vm.id, "state": vm.state}

    async def _run_resize_instance(self, session, op_repo, op, plan, params, access, client):
        vm = await client.get_virtual_machine(params.instance_id)
        was_running = vm.running
        if not (vm.running or vm.stopped):
            raise ConflictError("the instance is in transition")
        if was_running:
            await self._check_cancel(session, op)
            await self._note(
                session,
                op_repo,
                op,
                "stopping",
                "为变更规格停止云主机。",
                "Stopping the instance for resize.",
            )
            await client.stop_virtual_machine(vm.id)
            op.cloudstack_job_id = client.last_job_id
        vm = await client.resize_virtual_machine(vm.id, params.offering_id)
        op.cloudstack_job_id = client.last_job_id
        if was_running:
            await self._note(
                session,
                op_repo,
                op,
                "starting",
                "变更完成，恢复云主机运行。",
                "Resize finished; restarting.",
            )
            vm = await client.start_virtual_machine(vm.id)
            op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [vm.id]
        op.result = {
            "instance_id": vm.id,
            "offering_id": vm.serviceofferingid,
            "state": vm.state,
        }

    async def _run_delete_instance(self, session, op_repo, op, plan, params, access, client):
        vm = await client.get_virtual_machine(params.instance_id)
        await self._check_cancel(session, op)
        await client.destroy_virtual_machine(vm.id, expunge=params.expunge)
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [vm.id]
        failures = []
        for volume_id in params.delete_volume_ids:
            try:
                await client.delete_volume(volume_id)
            except CloudStackError:
                failures.append(volume_id)
        op.result = {
            "instance_id": vm.id,
            "expunged": params.expunge,
            "volumes_not_deleted": failures,
        }
        if failures:
            raise ReconciliationRequired("the instance was destroyed but some volumes remain")

    async def _run_restore_instance(self, session, op_repo, op, plan, params, access, client):
        await self._check_cancel(session, op)
        result = await client.restore_virtual_machine(params.instance_id, params.template_id)
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [params.instance_id]
        op.result = {"instance_id": params.instance_id, "result": result}

    async def _run_reinstall_instance(self, session, op_repo, op, plan, params, access, client):
        await self._check_cancel(session, op)
        result = await client.reinstall_virtual_machine(params.instance_id, params.template_id)
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [params.instance_id]
        op.result = {"instance_id": params.instance_id, "result": result}

    async def _run_create_volume(self, session, op_repo, op, plan, params, access, client):
        if params.snapshot_id:
            snapshots = await client.list_snapshots()
            snapshot = next((item for item in snapshots if item.id == params.snapshot_id), None)
            if snapshot is None:
                raise NotFoundError("the snapshot was not found")
            zone_id = snapshot.zoneid or params.zone_id
        else:
            zone_id = params.zone_id
        volume = await client.create_volume(
            name=params.name,
            zone_id=zone_id,
            size_gib=params.size_gib,
            disk_offering_id=params.disk_offering_id,
            snapshot_id=params.snapshot_id,
            account_id=access.account_param(plan.account_id),
            project_id=plan.project_id,
        )
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [volume.id]
        op.result = {"volume_id": volume.id, "name": volume.name, "size_gib": volume.size_gib}

    async def _run_attach_volume(self, session, op_repo, op, plan, params, access, client):
        volume = await client.attach_volume(params.volume_id, params.instance_id)
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [volume.id, params.instance_id]
        op.result = {"volume_id": volume.id, "instance_id": params.instance_id}

    async def _run_detach_volume(self, session, op_repo, op, plan, params, access, client):
        volume = await client.detach_volume(params.volume_id)
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [volume.id]
        op.result = {"volume_id": volume.id}

    async def _run_resize_volume(self, session, op_repo, op, plan, params, access, client):
        volume = await client.resize_volume(params.volume_id, params.size_gib)
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [volume.id]
        op.result = {"volume_id": volume.id, "size_gib": volume.size_gib}

    async def _run_delete_volume(self, session, op_repo, op, plan, params, access, client):
        await self._check_cancel(session, op)
        await client.delete_volume(params.volume_id)
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [params.volume_id]
        op.result = {"volume_id": params.volume_id}

    async def _run_create_snapshot(self, session, op_repo, op, plan, params, access, client):
        snapshot = await client.create_snapshot(params.volume_id, params.description)
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [snapshot.id]
        op.result = {"snapshot_id": snapshot.id, "volume_id": params.volume_id}

    async def _run_restore_snapshot(self, session, op_repo, op, plan, params, access, client):
        snapshots = await client.list_snapshots()
        snapshot = next((item for item in snapshots if item.id == params.snapshot_id), None)
        if snapshot is None:
            raise NotFoundError("the snapshot was not found")
        if not snapshot.zoneid:
            raise ValidationFailure("the snapshot has no zone")
        name = f"restored-{snapshot.id[:8]}"
        volume = await client.create_volume(
            name=name,
            zone_id=snapshot.zoneid,
            size_gib=0,
            snapshot_id=snapshot.id,
            account_id=access.account_param(plan.account_id),
            project_id=plan.project_id,
        )
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [volume.id]
        op.result = {"snapshot_id": snapshot.id, "volume_id": volume.id, "name": name}

    async def _run_create_network(self, session, op_repo, op, plan, params, access, client):
        network = await client.create_network(
            name=params.name,
            display_text=params.display_text,
            zone_id=params.zone_id,
            network_offering_id=params.network_offering_id,
            cidr=params.cidr,
            gateway=params.gateway,
            account_id=access.account_param(plan.account_id),
            project_id=plan.project_id,
        )
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [network.id]
        op.result = {"network_id": network.id, "name": network.name}

    async def _run_delete_network(self, session, op_repo, op, plan, params, access, client):
        await self._check_cancel(session, op)
        await client.delete_network(params.network_id)
        op.cloudstack_job_id = client.last_job_id
        op.resource_ids = [params.network_id]
        op.result = {"network_id": params.network_id}

    async def _run_update_firewall(self, session, op_repo, op, plan, params, access, client):
        added = []
        for rule in params.add:
            await self._check_cancel(session, op)
            result = await client.create_firewall_rule(
                network_id=params.network_id,
                protocol=rule.protocol,
                start_port=rule.start_port,
                end_port=rule.end_port,
                cidrs=rule.source_cidrs,
                action=rule.action,
                description=rule.description,
            )
            rule_id = result.get("id") or result.get("firewallrule", {}).get("id")
            if rule_id:
                added.append(str(rule_id))
        removed = []
        for rule_id in params.remove_rule_ids:
            await self._check_cancel(session, op)
            await client.delete_firewall_rule(rule_id)
            removed.append(rule_id)
        op.resource_ids = [params.network_id]
        op.result = {"network_id": params.network_id, "added": added, "removed": removed}

    async def _run_register_ssh_key(self, session, op_repo, op, plan, params, access, client):
        result = await client.register_ssh_key(params.name, params.public_key)
        op.cloudstack_job_id = client.last_job_id
        op.result = {"keypair_name": params.name, "result": result}
        op.resource_ids = []

    async def _run_guest_task(self, session, op_repo, op, plan, params, access, client):
        raise CapabilityUnsupported("managed guest tasks require an isolated guest executor; none is configured")

    # -- state helpers -----------------------------------------------------
    async def _step(
        self,
        session: AsyncSession,
        repo: OperationRepository,
        operation: Operation,
        name: str,
        zh: str,
        en: str,
    ) -> None:
        await self._note(session, repo, operation, name, zh, en)

    async def _note(
        self,
        session: AsyncSession,
        repo: OperationRepository,
        operation: Operation,
        name: str,
        zh: str,
        en: str,
    ) -> None:
        step = next((item for item in operation.steps if item.name == name), None)
        if step is None:
            step = OperationStep(
                step_id=f"{operation.operation_id}-{len(operation.steps) + 1}",
                name=name,
                state=StepState.RUNNING,
                started_at=utcnow(),
            )
            operation.steps.append(step)
        operation.updated_at = utcnow()
        await repo.save(operation)
        await repo.append_event(
            operation.operation_id,
            operation.state,
            zh,
            en,
            data={"step": name},
        )
        await session.commit()

    async def _finish(
        self,
        session: AsyncSession,
        repo: OperationRepository,
        operation: Operation,
        state: OperationState,
        zh: str,
        en: str,
        *,
        result: dict | None = None,
    ) -> None:
        # Persist the handler's resource IDs/results before taking a fresh
        # row snapshot; the snapshot must not discard unsaved progress.
        await repo.save(operation)
        operation = await repo.get_for_update(operation.operation_id) or operation
        for step in operation.steps:
            if step.state is StepState.RUNNING:
                step.state = StepState.SUCCEEDED
                step.finished_at = utcnow()
        operation.state = state
        if result:
            operation.result = {**operation.result, **result}
        operation.updated_at = utcnow()
        operation.finished_at = utcnow() if state.terminal else operation.finished_at
        await repo.save(operation)
        await repo.append_event(operation.operation_id, state, zh, en)
        await session.commit()

    async def _fail(
        self,
        session: AsyncSession,
        repo: OperationRepository,
        operation: Operation,
        code: str,
        zh: str,
    ) -> None:
        await repo.save(operation)
        current = await repo.get_for_update(operation.operation_id) or operation
        current.state = OperationState.FAILED
        current.error_code = code
        current.error_message = zh[:500]
        current.updated_at = utcnow()
        current.finished_at = utcnow()
        for step in current.steps:
            if step.state is StepState.RUNNING:
                step.state = StepState.FAILED
                step.finished_at = utcnow()
        await repo.save(current)
        await repo.append_event(
            current.operation_id,
            OperationState.FAILED,
            zh,
            "The operation failed; no further cloud changes will be submitted.",
            data={"error_code": code},
        )
        await session.commit()

    async def _reconcile_required(
        self,
        session: AsyncSession,
        repo: OperationRepository,
        operation: Operation,
        exc: Exception,
    ) -> None:
        await repo.save(operation)
        current = await repo.get_for_update(operation.operation_id) or operation
        current.state = OperationState.NEEDS_RECONCILIATION
        current.needs_reconciliation_reason = str(exc)[:500] or "cloud outcome uncertain"
        current.error_code = getattr(exc, "code", "needs_reconciliation")
        current.updated_at = utcnow()
        for step in current.steps:
            if step.state is StepState.RUNNING:
                step.state = StepState.UNKNOWN
                step.finished_at = utcnow()
        await repo.save(current)
        await repo.append_event(
            current.operation_id,
            OperationState.NEEDS_RECONCILIATION,
            "云端结果不确定：操作已标记为待核对，不会自动重试。",
            "Cloud outcome uncertain: marked for reconciliation and not retried automatically.",
            data={"reason": current.needs_reconciliation_reason},
        )
        await session.commit()

    async def cancel(self, operation_id: str, *, subject: str) -> Operation:
        async with self.session_factory() as session:
            repo = OperationRepository(session)
            operation = await repo.get_for_update(operation_id)
            if operation is None:
                raise NotFoundError("operation not found")
            if operation.principal_subject != subject:
                raise AuthorizationError("this operation belongs to another principal")
            if operation.terminal:
                raise ConflictError("the operation has already finished")
            if operation.state is OperationState.QUEUED:
                operation.state = OperationState.CANCELLED
                operation.finished_at = utcnow()
                operation.updated_at = utcnow()
                await repo.save(operation)
                await repo.append_event(
                    operation_id,
                    OperationState.CANCELLED,
                    "已取消，尚未提交云端请求。",
                    "Cancelled before any cloud request was submitted.",
                )
            else:
                operation.cancel_requested_at = utcnow()
                operation.state = OperationState.CANCEL_REQUESTED
                operation.updated_at = utcnow()
                await repo.save(operation)
                await repo.append_event(
                    operation_id,
                    OperationState.CANCEL_REQUESTED,
                    "已请求取消；已提交的云端操作不会被回滚。",
                    "Cancellation requested; already-submitted cloud work is not undone.",
                )
            await session.commit()
            return operation

    async def reconcile(self, operation_id: str, *, subject: str) -> Operation:
        """Best-effort reconciliation for creates with an uncertain outcome."""

        async with self.session_factory() as session:
            repo = OperationRepository(session)
            plan_repo = PlanRepository(session)
            operation = await repo.get_for_update(operation_id)
            if operation is None:
                raise NotFoundError("operation not found")
            if operation.principal_subject != subject:
                raise AuthorizationError("this operation belongs to another principal")
            if operation.state is not OperationState.NEEDS_RECONCILIATION:
                raise ConflictError("the operation is not awaiting reconciliation")
            plan = await plan_repo.get(operation.plan_id)
            if plan is None:
                raise NotFoundError("the plan no longer exists")
            principal = await load_principal(session, self.settings, operation.principal_subject)
            access = CloudAccess(session, self.settings, self.vault, principal)
            confirmed: str | None = None
            try:
                async with access.client() as client:
                    if plan.action.value == "create_instance":
                        name = str(plan.parameters.get("name", ""))
                        vms = await client.list_virtual_machines(account_id=access.account_param(plan.account_id))
                        vm_matches = [vm for vm in vms if vm.name == name]
                        if len(vm_matches) == 1:
                            confirmed = vm_matches[0].id
                    elif plan.action.value == "create_volume":
                        name = str(plan.parameters.get("name", ""))
                        volumes = await client.list_volumes(account_id=access.account_param(plan.account_id))
                        volume_matches = [item for item in volumes if item.name == name]
                        if len(volume_matches) == 1:
                            confirmed = volume_matches[0].id
            except Exception:
                logger.exception("reconciliation query failed for %s", operation_id)
            if confirmed:
                operation.resource_ids = [confirmed]
                operation.state = OperationState.SUCCEEDED
                operation.finished_at = utcnow()
                operation.updated_at = utcnow()
                operation.needs_reconciliation_reason = None
                await repo.save(operation)
                await repo.append_event(
                    operation_id,
                    OperationState.SUCCEEDED,
                    "核对完成：找到唯一匹配资源，标记为成功。",
                    "Reconciled: exactly one matching resource was found; marked succeeded.",
                    data={"resource_id": confirmed},
                )
            else:
                operation.updated_at = utcnow()
                operation.needs_reconciliation_reason = (
                    "no unique matching resource was found; operator review required"
                )
                await repo.save(operation)
                await repo.append_event(
                    operation_id,
                    OperationState.NEEDS_RECONCILIATION,
                    "核对未找到唯一匹配资源，仍需人工处理。",
                    "Reconciliation found no unique match; manual review is still required.",
                )
            await session.commit()
            return operation
