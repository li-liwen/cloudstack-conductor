"""Protocols injected into the identity migration service.

The service is deliberately free of SQLAlchemy, httpx, and FastAPI details so
the state machine can be tested with in-memory doubles. Production adapters
live in :mod:`conductor.identity.store`, :mod:`conductor.identity.authentik`,
:mod:`conductor.identity.cloudstack_login`, and
:mod:`conductor.identity.provisioner`.
"""

from __future__ import annotations

from typing import Any, Protocol

from conductor.identity.models import AdvanceResult, AuthentikUser, MigrationState
from conductor.models.enums import MappingStage


class MigrationStore(Protocol):
    async def load(self, subject: str) -> MigrationState | None: ...

    async def advance(
        self,
        subject: str,
        expected: MappingStage,
        target: MappingStage,
        *,
        saml_provider_id: str | None = None,
        evidence_hash: str | None = None,
        legacy_session_closed: bool | None = None,
    ) -> AdvanceResult: ...

    async def record_failure(self, subject: str, code: str) -> None: ...

    async def mark_session_closed(self, subject: str, *, closed: bool = True) -> None: ...

    async def audit(
        self,
        subject: str,
        *,
        action: str,
        outcome: str,
        detail: dict[str, Any] | None = None,
    ) -> None: ...


class AuthentikAdmin(Protocol):
    async def find_user_by_subject(self, subject: str) -> AuthentikUser | None: ...

    async def set_password(self, subject: str, password: str) -> None: ...

    async def mfa_ready(self, subject: str) -> bool: ...

    async def revoke_sessions(self, subject: str) -> int: ...

    async def aclose(self) -> None: ...


class LegacySession(Protocol):
    """A temporary, customer-authenticated CloudStack session.

    The session key is a bearer value. It is held only in process memory and is
    never persisted, audited, or logged.
    """

    user_id: str
    account_id: str
    domain_id: str
    session_key: str
    mfa_enabled: bool
    mfa_verified: bool

    async def logout(self) -> None: ...

    async def aclose(self) -> None: ...


class CloudStackLogin(Protocol):
    async def native_login(
        self,
        username: str,
        password: str,
        domain_id: str,
        otp: str | None = None,
    ) -> LegacySession: ...


class SamlProvisioner(Protocol):
    async def authorize_saml_sso(
        self,
        *,
        user_id: str,
        entity_id: str,
        enable: bool = True,
    ) -> bool: ...

    async def aclose(self) -> None: ...


class SessionRegistry(Protocol):
    async def put(self, subject: str, session: LegacySession) -> None: ...

    async def peek(self, subject: str) -> LegacySession | None: ...

    async def pop(self, subject: str) -> LegacySession | None: ...

    async def close_all(self) -> None: ...
