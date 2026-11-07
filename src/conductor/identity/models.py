"""Identity-bridge value types shared by the service, routes, and adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from conductor.models.enums import MappingStage
from conductor.models.principal import IdentityMapping


@dataclass(frozen=True)
class AuthentikUser:
    """The stable subset of an authentik user used by the bridge.

    ``subject`` is resolved only from the authentik user UUID, UID, or numeric
    primary key. Usernames and email addresses are descriptive and are never
    used to match an identity.
    """

    pk: int
    uuid: str
    uid: str
    username: str
    name: str
    is_active: bool
    groups: tuple[str, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MigrationState:
    mapping: IdentityMapping
    stage: MappingStage
    attempts: int = 0
    last_error: str | None = None
    legacy_session_closed: bool = False
    evidence_hash: str | None = None
    saml_provider_id: str | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class AdvanceResult:
    ok: bool
    stage: MappingStage | None


NextStep = Literal["legacy-verify", "complete-mfa", "finish", "none"]


class MigrationStatus(BaseModel):
    """Public migration view. It never contains passwords, OTPs, or tokens."""

    model_config = ConfigDict(extra="forbid")

    stage: MappingStage
    next_step: NextStep
    required_mfa: bool
    saml_provider_configured: bool
    attempts: int = Field(default=0, ge=0)
    last_error_code: str | None = None
    legacy_session_closed: bool = False
    updated_at: datetime | None = None
