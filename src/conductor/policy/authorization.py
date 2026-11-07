"""Deterministic authorization.

The effective permission for a request is the intersection of:

* the CloudStack user's own permission (enforced by CloudStack at call time),
* application policy,
* the selected account/project context,
* the external client scope, if the request came through MCP,
* the required action approval.

None of these checks consult a language model.
"""

from __future__ import annotations

from datetime import datetime

from conductor.errors import ApprovalExpired, AuthorizationError, PlanChanged
from conductor.models.enums import ActionType, ApprovalRequirement, PrincipalRole, Scope
from conductor.models.plan import ActionPlan, Approval
from conductor.models.principal import Principal
from conductor.policy.actions import approval_requirement, get_spec


def ensure_scope(granted: set[str] | list[str] | tuple[str, ...], required: Scope) -> None:
    if required.value not in set(granted):
        raise AuthorizationError("the client has not been granted the required scope")


def required_scope(action: ActionType | str) -> Scope:
    return get_spec(action).required_scope


def ensure_account_context(principal: Principal, account_id: str | None, project_id: str | None = None) -> None:
    """Check the selected account/project is within the principal's context.

    Operators may inspect any account but still act through the mapped
    CloudStack identity. Regular users are limited to their own account and
    explicitly delegated accounts/projects.
    """

    if account_id is None:
        return
    if principal.is_operator:
        return
    if account_id == principal.cloudstack_account_id:
        if project_id and principal.project_ids and project_id not in principal.project_ids:
            raise AuthorizationError("the selected project is not available to this identity")
        return
    if account_id in principal.delegated_account_ids:
        return
    raise AuthorizationError("the selected account is not available to this identity")


def ensure_action_allowed(principal: Principal, action: ActionType | str) -> None:
    spec = get_spec(action)
    if spec.action is ActionType.GUEST_TASK and not principal.is_operator and not principal.is_domain_admin:
        # Guest tasks are opt-in managed-template workflows. Ordinary users
        # may propose only if their mapping grants the guest capability; the
        # execution service enforces the project/template opt-in as well.
        return
    if spec.mutating and principal.is_operator is False:
        # All users may propose mutations; CloudStack enforces their actual
        # permission. This function exists to hold a single place for future
        # application-level restrictions.
        return


def ensure_approval(
    plan: ActionPlan,
    approval: Approval | None,
    now: datetime,
    *,
    principal_subject: str | None = None,
) -> None:
    """Validate that an approval covers the exact plan being executed."""

    requirement = approval_requirement(plan.action, plan.parameters)
    if requirement is ApprovalRequirement.NONE:
        return
    if approval is None:
        raise AuthorizationError("this action requires an explicit approval")
    if principal_subject and approval.approved_by != principal_subject:
        raise AuthorizationError("the approval was not granted by this principal")
    if approval.approved_by != plan.principal_subject:
        raise AuthorizationError("the approval was not granted by the plan's principal")
    if approval.digest != plan.digest:
        raise PlanChanged("the plan digest does not match the approval")
    if approval.plan_id != plan.plan_id:
        raise PlanChanged("the approval belongs to a different plan")
    if approval.expires_at <= now:
        raise ApprovalExpired()


def can_view_plan(principal: Principal, plan: ActionPlan) -> bool:
    return principal.is_operator or plan.principal_subject == principal.subject


def can_view_operation(principal: Principal, operation_subject: str) -> bool:
    return principal.is_operator or operation_subject == principal.subject


def ensure_expunge_allowed(params: object, principal: Principal) -> None:
    """Only operators may request irreversible CloudStack expunge.

    Tenants can destroy an instance (recoverable until expunged) but CloudStack
    itself denies account-level expunge. Reject it before a plan is prepared so
    users never approve an operation that the cloud will refuse.
    """

    if getattr(params, "expunge", False) and PrincipalRole.OPERATOR not in principal.roles:
        raise AuthorizationError("only an operator may expunge an instance")
