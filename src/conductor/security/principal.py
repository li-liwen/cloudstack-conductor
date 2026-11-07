"""Principal resolution from verified identity claims.

The mapping is the authority for CloudStack identity. There is no implicit
matching on username, email, or display name.
"""

from __future__ import annotations

from typing import Any, Protocol

from conductor.config import Settings
from conductor.errors import AuthenticationError, AuthorizationError
from conductor.models.enums import MappingStage, PrincipalRole
from conductor.models.principal import IdentityMapping, Principal
from conductor.security.oidc import mfa_satisfied


class MappingLookup(Protocol):
    async def get_mapping(self, subject: str) -> IdentityMapping | None: ...


class PrincipalService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def resolve_claims(self, claims: dict[str, Any], mappings: MappingLookup) -> Principal:
        subject = str(claims.get("sub") or "")
        if not subject:
            raise AuthenticationError("the identity has no stable subject")
        mapping = await mappings.get_mapping(subject)
        if mapping is None:
            raise AuthenticationError(
                "this identity is not provisioned; contact an administrator to bind it to a CloudStack user"
            )
        self._validate_mapping(mapping)
        if mapping.stage is not MappingStage.COMPLETE:
            raise AuthorizationError("identity migration is not complete; use the account migration flow")
        if mapping.mfa_required and not mfa_satisfied(claims, self.settings.auth.mfa_claim):
            raise AuthenticationError("multi-factor authentication is required")
        groups = _groups(claims, self.settings.auth.oidc_groups_claim)
        roles = set(mapping.roles)
        group_set = set(groups)
        if group_set & set(self.settings.auth.operator_groups):
            roles.add(PrincipalRole.OPERATOR)
        if group_set & set(self.settings.auth.domain_admin_groups):
            roles.add(PrincipalRole.DOMAIN_ADMIN)
        return self._principal_from_mapping(
            mapping,
            roles=sorted(roles, key=lambda item: item.value),
            groups=groups,
            amr=_amr(claims),
            mfa=mapping.mfa_required,
        )

    async def resolve_demo(self, subject: str, mappings: MappingLookup) -> Principal:
        if not (self.settings.auth.mode == "demo" and self.settings.auth.allow_demo_auth):
            raise AuthenticationError("demo authentication is not enabled")
        mapping = await mappings.get_mapping(subject)
        if mapping is None:
            raise AuthenticationError("demo identity is not provisioned")
        self._validate_mapping(mapping)
        return self._principal_from_mapping(
            mapping,
            roles=sorted(mapping.roles, key=lambda item: item.value),
            groups=[],
            amr=["pwd"],
            mfa=False,
        )

    def build_principal(
        self,
        mapping: IdentityMapping,
        *,
        roles: list[PrincipalRole] | None = None,
        groups: list[str] | None = None,
        amr: list[str] | None = None,
        mfa: bool = False,
    ) -> Principal:
        """Reconstruct a principal from a stored mapping (trusted calls only)."""

        self._validate_mapping(mapping)
        return self._principal_from_mapping(
            mapping,
            roles=roles or sorted(mapping.roles, key=lambda item: item.value),
            groups=groups or [],
            amr=amr or [],
            mfa=mfa,
        )

    @staticmethod
    def _validate_mapping(mapping: IdentityMapping) -> None:
        required = (
            mapping.cloud_id,
            mapping.cloudstack_user_id,
            mapping.cloudstack_account_id,
            mapping.cloudstack_domain_id,
            mapping.cloudstack_domain,
            mapping.cloudstack_username,
            mapping.account,
        )
        if not all(required):
            raise AuthenticationError("the identity mapping is incomplete; contact an administrator")

    @staticmethod
    def _principal_from_mapping(
        mapping: IdentityMapping,
        *,
        roles: list[PrincipalRole],
        groups: list[str],
        amr: list[str],
        mfa: bool,
    ) -> Principal:
        return Principal(
            subject=mapping.subject,
            username=mapping.cloudstack_username,
            display_name=mapping.display_name or mapping.cloudstack_username,
            email=mapping.email,
            cloud_id=mapping.cloud_id,
            cloudstack_user_id=mapping.cloudstack_user_id,
            cloudstack_account_id=mapping.cloudstack_account_id,
            cloudstack_domain_id=mapping.cloudstack_domain_id,
            cloudstack_domain=mapping.cloudstack_domain,
            cloudstack_username=mapping.cloudstack_username,
            account=mapping.account,
            domain_ancestors=list(mapping.domain_ancestors),
            roles=roles or [PrincipalRole.USER],
            project_ids=list(mapping.project_ids),
            delegated_account_ids=list(mapping.delegated_account_ids),
            groups=groups,
            amr=amr,
            mapping_version=mapping.mapping_version,
        )


def _groups(claims: dict[str, Any], claim_name: str) -> list[str]:
    raw = claims.get(claim_name)
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, str)]
    return []


def _amr(claims: dict[str, Any]) -> list[str]:
    raw = claims.get("amr") or []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []
