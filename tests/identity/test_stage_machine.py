"""The migration state machine is fixed and forward-only."""

from __future__ import annotations

import pytest

from conductor.identity.stages import (
    STAGE_ORDER,
    can_advance,
    is_at_least,
    next_stage,
    next_step,
    required_mfa,
)
from conductor.models.enums import MappingStage, PrincipalRole
from conductor.models.principal import IdentityMapping


def test_stage_order_is_exact() -> None:
    assert STAGE_ORDER == (
        MappingStage.UNMIGRATED,
        MappingStage.LEGACY_CREDENTIALS_VERIFIED,
        MappingStage.IDP_CREDENTIALS_READY,
        MappingStage.REQUIRED_MFA_READY,
        MappingStage.CLOUDSTACK_SAML_AUTHORIZED,
        MappingStage.COMPLETE,
    )


def test_only_the_next_stage_is_allowed() -> None:
    for index, stage in enumerate(STAGE_ORDER[:-1]):
        expected = STAGE_ORDER[index + 1]
        assert can_advance(stage, expected)
        for other in STAGE_ORDER:
            if other is not expected:
                assert not can_advance(stage, other)
    assert not can_advance(MappingStage.COMPLETE, MappingStage.COMPLETE)


def test_next_stage_and_at_least() -> None:
    assert next_stage(MappingStage.UNMIGRATED) is MappingStage.LEGACY_CREDENTIALS_VERIFIED
    assert next_stage(MappingStage.CLOUDSTACK_SAML_AUTHORIZED) is MappingStage.COMPLETE
    assert next_stage(MappingStage.COMPLETE) is None
    assert is_at_least(MappingStage.COMPLETE, MappingStage.IDP_CREDENTIALS_READY)
    assert not is_at_least(MappingStage.UNMIGRATED, MappingStage.LEGACY_CREDENTIALS_VERIFIED)


def test_next_step_is_monotonic() -> None:
    assert [next_step(stage) for stage in STAGE_ORDER] == [
        "legacy-verify",
        "legacy-verify",
        "complete-mfa",
        "finish",
        "finish",
        "none",
    ]


@pytest.mark.parametrize(
    ("roles", "mfa_required", "legacy_2fa", "expected"),
    [
        ([PrincipalRole.USER], False, False, False),
        ([PrincipalRole.USER], True, False, True),
        ([PrincipalRole.USER], False, True, True),
        ([PrincipalRole.OPERATOR], False, False, True),
        ([PrincipalRole.DOMAIN_ADMIN], False, False, True),
    ],
)
def test_required_mfa(
    roles: list[PrincipalRole],
    mfa_required: bool,
    legacy_2fa: bool,
    expected: bool,
    mapping: IdentityMapping,
) -> None:
    candidate = mapping.model_copy(update={"roles": roles, "mfa_required": mfa_required, "legacy_2fa": legacy_2fa})
    assert required_mfa(candidate) is expected
