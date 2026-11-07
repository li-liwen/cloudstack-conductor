"""Principal and identity-mapping contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from conductor.models.common import ensure_utc
from conductor.models.enums import MappingStage, PrincipalRole


class Principal(BaseModel):
    """An authenticated application identity.

    ``subject`` is the stable identity-provider subject. The CloudStack
    identifiers are bound through a pre-provisioned mapping and are never
    inferred by merging usernames or email addresses.
    """

    subject: str = Field(min_length=1)
    username: str = Field(min_length=1)
    display_name: str = ""
    email: str | None = None
    cloud_id: str = Field(min_length=1)
    cloudstack_user_id: str
    cloudstack_account_id: str
    cloudstack_domain_id: str
    cloudstack_domain: str
    cloudstack_username: str
    account: str
    domain_ancestors: list[str] = Field(default_factory=list)
    roles: list[PrincipalRole] = Field(default_factory=lambda: [PrincipalRole.USER])
    project_ids: list[str] = Field(default_factory=list)
    delegated_account_ids: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    amr: list[str] = Field(default_factory=list)
    mapping_version: str = "1"

    @property
    def is_operator(self) -> bool:
        return PrincipalRole.OPERATOR in self.roles

    @property
    def is_domain_admin(self) -> bool:
        return PrincipalRole.DOMAIN_ADMIN in self.roles or self.is_operator

    @property
    def tenant_key(self) -> str:
        return f"{self.cloud_id}:{self.cloudstack_domain_id}"

    @property
    def account_key(self) -> str:
        return f"{self.cloud_id}:{self.cloudstack_account_id}"


class IdentityMapping(BaseModel):
    """Pre-provisioned binding between an IdP subject and a CloudStack user."""

    subject: str
    cloud_id: str
    cloudstack_user_id: str
    cloudstack_account_id: str
    cloudstack_domain_id: str
    cloudstack_domain: str
    cloudstack_username: str
    account: str
    domain_ancestors: list[str] = Field(default_factory=list)
    mapping_version: str = "1"
    stage: MappingStage = MappingStage.UNMIGRATED
    saml_provider_id: str | None = None
    roles: list[PrincipalRole] = Field(default_factory=lambda: [PrincipalRole.USER])
    project_ids: list[str] = Field(default_factory=list)
    delegated_account_ids: list[str] = Field(default_factory=list)
    mfa_required: bool = False
    legacy_2fa: bool = False
    display_name: str = ""
    email: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value else None


class SessionInfo(BaseModel):
    principal: Principal
    expires_at: datetime
    csrf_token: str
    mfa: bool = False

    @field_validator("expires_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)
