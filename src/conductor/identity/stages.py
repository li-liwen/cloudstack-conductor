"""The password-migration state machine.

The order is intentionally fixed and every transition moves exactly one step
forward. A request that arrives for an already-completed step is idempotent;
it never moves the stage backwards and never resets a password.
"""

from __future__ import annotations

from conductor.models.enums import MappingStage, PrincipalRole
from conductor.models.principal import IdentityMapping

STAGE_ORDER: tuple[MappingStage, ...] = (
    MappingStage.UNMIGRATED,
    MappingStage.LEGACY_CREDENTIALS_VERIFIED,
    MappingStage.IDP_CREDENTIALS_READY,
    MappingStage.REQUIRED_MFA_READY,
    MappingStage.CLOUDSTACK_SAML_AUTHORIZED,
    MappingStage.COMPLETE,
)

ALLOWED_TRANSITIONS: dict[MappingStage, frozenset[MappingStage]] = {
    MappingStage.UNMIGRATED: frozenset({MappingStage.LEGACY_CREDENTIALS_VERIFIED}),
    MappingStage.LEGACY_CREDENTIALS_VERIFIED: frozenset({MappingStage.IDP_CREDENTIALS_READY}),
    MappingStage.IDP_CREDENTIALS_READY: frozenset({MappingStage.REQUIRED_MFA_READY}),
    MappingStage.REQUIRED_MFA_READY: frozenset({MappingStage.CLOUDSTACK_SAML_AUTHORIZED}),
    MappingStage.CLOUDSTACK_SAML_AUTHORIZED: frozenset({MappingStage.COMPLETE}),
    MappingStage.COMPLETE: frozenset(),
}


def stage_index(stage: MappingStage) -> int:
    return STAGE_ORDER.index(stage)


def is_at_least(stage: MappingStage, minimum: MappingStage) -> bool:
    return stage_index(stage) >= stage_index(minimum)


def next_stage(stage: MappingStage) -> MappingStage | None:
    if stage is MappingStage.COMPLETE:
        return None
    return STAGE_ORDER[stage_index(stage) + 1]


def can_advance(current: MappingStage, target: MappingStage) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def required_mfa(mapping: IdentityMapping) -> bool:
    """MFA is required for privileged roles and identities that had legacy 2FA.

    A domain administrator is privileged even if the mapping omits the operator
    role, mirroring :attr:`conductor.models.principal.Principal.is_domain_admin`.
    """

    roles = set(mapping.roles)
    privileged = bool(roles & {PrincipalRole.OPERATOR, PrincipalRole.DOMAIN_ADMIN})
    return bool(mapping.mfa_required or mapping.legacy_2fa or privileged)


def next_step(stage: MappingStage) -> str:
    if stage is MappingStage.UNMIGRATED:
        return "legacy-verify"
    if stage is MappingStage.LEGACY_CREDENTIALS_VERIFIED:
        # A previous attempt verified the legacy password but did not finish
        # writing the IdP credential; the same step resumes safely.
        return "legacy-verify"
    if stage is MappingStage.IDP_CREDENTIALS_READY:
        return "complete-mfa"
    if stage in {MappingStage.REQUIRED_MFA_READY, MappingStage.CLOUDSTACK_SAML_AUTHORIZED}:
        return "finish"
    return "none"
