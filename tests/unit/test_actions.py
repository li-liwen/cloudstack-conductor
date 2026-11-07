from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from conductor.errors import ApprovalExpired, AuthorizationError, PlanChanged, ValidationFailure
from conductor.models.enums import ActionType, ApprovalRequirement, GuestTaskType, PrincipalRole
from conductor.models.plan import ActionPlan, Approval
from conductor.models.principal import Principal
from conductor.policy.actions import (
    CreateNetworkParams,
    DeleteInstanceParams,
    FirewallRuleSpec,
    GuestTaskParams,
    RegisterSshKeyParams,
    approval_requirement,
    validate_params,
)
from conductor.policy.authorization import ensure_account_context, ensure_approval, ensure_expunge_allowed


def test_approval_matrix_matches_the_disclosed_policy() -> None:
    assert approval_requirement(ActionType.QUOTE, {}) is ApprovalRequirement.NONE
    assert approval_requirement(ActionType.LIST_RESOURCES, {}) is ApprovalRequirement.NONE
    assert approval_requirement(ActionType.OPEN_CONSOLE, {}) is ApprovalRequirement.NONE
    assert approval_requirement(ActionType.CREATE_INSTANCE, {}) is ApprovalRequirement.REQUIRED
    assert approval_requirement(ActionType.START_INSTANCE, {}) is ApprovalRequirement.REQUIRED
    assert approval_requirement(ActionType.RESIZE_INSTANCE, {}) is ApprovalRequirement.REQUIRED
    assert approval_requirement(ActionType.STOP_INSTANCE, {}) is ApprovalRequirement.REQUIRED
    assert approval_requirement(ActionType.REBOOT_INSTANCE, {}) is ApprovalRequirement.REQUIRED
    assert approval_requirement(ActionType.UPDATE_FIREWALL, {}) is ApprovalRequirement.REQUIRED
    assert approval_requirement(ActionType.DELETE_INSTANCE, {}) is ApprovalRequirement.EXPLICIT
    assert approval_requirement(ActionType.DELETE_VOLUME, {}) is ApprovalRequirement.EXPLICIT
    assert approval_requirement(ActionType.RESTORE_INSTANCE, {}) is ApprovalRequirement.EXPLICIT
    assert approval_requirement(ActionType.GUEST_TASK, {}) is ApprovalRequirement.EXPLICIT


def test_params_forbid_extra_fields() -> None:
    with pytest.raises(ValidationFailure):
        validate_params(
            ActionType.START_INSTANCE,
            {
                "instance_id": "11111111-1111-4111-8111-111111111111",
                "admin_override": True,
            },
        )


def test_ssh_key_rejects_private_material() -> None:
    with pytest.raises(ValidationError):
        RegisterSshKeyParams(
            name="demo-key",
            public_key=("-----BEGIN " + "OPENSSH PRIVATE KEY-----\nAAAA\n-----END " + "OPENSSH PRIVATE KEY-----"),
        )


def test_ssh_key_accepts_a_public_key() -> None:
    import base64

    encoded = base64.b64encode(b"synthetic-public-key-material").decode()
    key = f"ssh-ed25519 {encoded} demo@example.com"
    params = RegisterSshKeyParams(name="demo-key", public_key=key)
    assert params.public_key.startswith("ssh-ed25519 ")


def test_guest_task_rejects_shell_metacharacters() -> None:
    with pytest.raises(ValidationError):
        GuestTaskParams(
            instance_id="11111111-1111-4111-8111-111111111111",
            task=GuestTaskType.EXPAND_FILESYSTEM,
            parameters={"device": "/dev/sda1; rm -rf /"},
        )


def test_firewall_rejects_invalid_cidr() -> None:
    with pytest.raises(ValidationError):
        FirewallRuleSpec(source_cidrs=["not-a-cidr"])
    rule = FirewallRuleSpec(source_cidrs=["203.0.113.0/24"], start_port=443, end_port=443)
    assert rule.source_cidrs == ["203.0.113.0/24"]


def test_network_rejects_public_cidr() -> None:
    with pytest.raises(ValidationError):
        CreateNetworkParams(
            name="demo",
            zone_id="44444444-4444-4444-8444-444444444444",
            network_offering_id="55555555-5555-4555-8555-555555555555",
            cidr="8.8.8.0/24",
        )
    allowed = CreateNetworkParams(
        name="demo",
        zone_id="44444444-4444-4444-8444-444444444444",
        network_offering_id="55555555-5555-4555-8555-555555555555",
        cidr="192.0.2.0/24",
    )
    assert allowed.cidr == "192.0.2.0/24"


def test_plan_digest_changes_with_material_fields() -> None:
    now = datetime(2026, 11, 7, tzinfo=UTC)
    plan = ActionPlan(
        plan_id="plan_test",
        principal_subject="subject",
        tenant_key="cloud:domain",
        cloud_id="cloud",
        account_id="account",
        action=ActionType.START_INSTANCE,
        parameters={"instance_id": "11111111-1111-4111-8111-111111111111"},
        created_at=now,
        expires_at=now + timedelta(hours=1),
        explanation_zh="启动",
    ).with_digest()
    same = plan.model_copy(update={"explanation_zh": "different wording"}).with_digest()
    assert same.digest == plan.digest, "explanation text is not material"
    changed = plan.model_copy(update={"parameters": {"instance_id": "other"}}).with_digest()
    assert changed.digest != plan.digest


def test_approval_must_match_digest_owner_and_expiry() -> None:
    now = datetime(2026, 11, 7, tzinfo=UTC)
    plan = ActionPlan(
        plan_id="plan_test",
        principal_subject="subject",
        tenant_key="cloud:domain",
        cloud_id="cloud",
        account_id="account",
        action=ActionType.DELETE_VOLUME,
        parameters={"volume_id": "11111111-1111-4111-8111-111111111111"},
        created_at=now,
        expires_at=now + timedelta(hours=1),
    ).with_digest()
    good = Approval(
        approval_id="apr",
        plan_id=plan.plan_id,
        digest=plan.digest,
        approved_by="subject",
        approved_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    ensure_approval(plan, good, now + timedelta(minutes=1))
    with pytest.raises(ApprovalExpired):
        ensure_approval(plan, good, now + timedelta(minutes=11))
    with pytest.raises(AuthorizationError):
        ensure_approval(plan, good.model_copy(update={"approved_by": "other"}), now)
    with pytest.raises(PlanChanged):
        ensure_approval(plan, good.model_copy(update={"digest": "0" * 64}), now)


def test_account_context_is_limited() -> None:
    principal = Principal(
        subject="s",
        username="u",
        cloud_id="cloud",
        cloudstack_user_id="user",
        cloudstack_account_id="account-1",
        cloudstack_domain_id="domain",
        cloudstack_domain="demo.example",
        cloudstack_username="u",
        account="account-1",
    )
    ensure_account_context(principal, "account-1")
    with pytest.raises(AuthorizationError):
        ensure_account_context(principal, "account-2")
    delegated = principal.model_copy(update={"delegated_account_ids": ["account-2"]})
    ensure_account_context(delegated, "account-2")


def test_only_operators_may_request_expunge() -> None:
    principal = Principal(
        subject="s",
        username="u",
        cloud_id="cloud",
        cloudstack_user_id="user",
        cloudstack_account_id="account-1",
        cloudstack_domain_id="domain",
        cloudstack_domain="ROOT",
        cloudstack_username="u",
        account="account",
        roles=[PrincipalRole.USER],
    )
    ensure_expunge_allowed(DeleteInstanceParams(instance_id="11111111-1111-4111-8111-111111111111"), principal)
    with pytest.raises(AuthorizationError):
        ensure_expunge_allowed(
            DeleteInstanceParams(instance_id="11111111-1111-4111-8111-111111111111", expunge=True), principal
        )
    operator = principal.model_copy(update={"roles": [PrincipalRole.OPERATOR]})
    ensure_expunge_allowed(
        DeleteInstanceParams(instance_id="11111111-1111-4111-8111-111111111111", expunge=True), operator
    )
