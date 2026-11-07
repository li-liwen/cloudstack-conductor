"""Synthetic fixtures for the identity-bridge tests.

Every identifier, hostname, and credential here is a placeholder. There is no
network access and no real CloudStack or authentik deployment.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from conductor.identity.config import DomainProviderMap, DomainSamlProvider, IdentityBridgeSettings
from conductor.identity.models import AdvanceResult, AuthentikUser, MigrationState
from conductor.identity.protocols import LegacySession
from conductor.identity.service import IdentityMigrationService
from conductor.models.enums import MappingStage, PrincipalRole
from conductor.models.principal import IdentityMapping

USER_ID = "11111111-1111-4111-8111-111111111111"
ACCOUNT_ID = "22222222-2222-4222-8222-222222222222"
DOMAIN_ID = "33333333-3333-4333-8333-333333333333"
SUBJECT = "synthetic-subject-1"
ENTITY_ID = "https://auth.example.com/application/saml/cloudstack-domain-one/metadata/"


@pytest.fixture
def when() -> datetime:
    return datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def mapping() -> IdentityMapping:
    return IdentityMapping(
        subject=SUBJECT,
        cloud_id="cloud-example",
        cloudstack_user_id=USER_ID,
        cloudstack_account_id=ACCOUNT_ID,
        cloudstack_domain_id=DOMAIN_ID,
        cloudstack_domain="domain-one",
        cloudstack_username="user.one",
        account="account-one",
        roles=[PrincipalRole.USER],
        mapping_version="1",
    )


@pytest.fixture
def settings() -> IdentityBridgeSettings:
    return IdentityBridgeSettings(
        enabled=True,
        authentik_base_url="https://auth.example.com",
        cloudstack_api_url="https://cloud.example.com/client/api",
        require_tls=True,
        close_legacy_session_immediately=True,
        legacy_session_ttl_seconds=300,
    )


@pytest.fixture
def providers() -> DomainProviderMap:
    return DomainProviderMap(
        domains={
            DOMAIN_ID: DomainSamlProvider(
                provider_id="saml-domain-one",
                entity_id=ENTITY_ID,
                metadata_url="https://auth.example.com/application/saml/cloudstack-domain-one/metadata/",
            )
        }
    )


class FakeStore:
    def __init__(self, mapping: IdentityMapping, *, stage: MappingStage = MappingStage.UNMIGRATED) -> None:
        self.mapping = mapping
        self.stage = stage
        self.attempts = 0
        self.last_error: str | None = None
        self.legacy_session_closed = False
        self.evidence_hash: str | None = None
        self.saml_provider_id: str | None = None
        self.audit_events: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    def _state(self) -> MigrationState:
        return MigrationState(
            mapping=self.mapping,
            stage=self.stage,
            attempts=self.attempts,
            last_error=self.last_error,
            legacy_session_closed=self.legacy_session_closed,
            evidence_hash=self.evidence_hash,
            saml_provider_id=self.saml_provider_id,
            updated_at=datetime(2026, 11, 7, 12, 0, tzinfo=UTC),
        )

    async def load(self, subject: str) -> MigrationState | None:
        if subject != self.mapping.subject:
            return None
        async with self._lock:
            return self._state()

    async def advance(
        self,
        subject: str,
        expected: MappingStage,
        target: MappingStage,
        *,
        saml_provider_id: str | None = None,
        evidence_hash: str | None = None,
        legacy_session_closed: bool | None = None,
    ) -> AdvanceResult:
        async with self._lock:
            if subject != self.mapping.subject or self.stage is not expected:
                return AdvanceResult(False, self.stage if subject == self.mapping.subject else None)
            self.stage = target
            self.mapping = self.mapping.model_copy(update={"stage": target})
            if saml_provider_id is not None:
                self.saml_provider_id = saml_provider_id
            if evidence_hash is not None:
                self.evidence_hash = evidence_hash
            if legacy_session_closed is not None:
                self.legacy_session_closed = legacy_session_closed
            self.audit_events.append({"action": "advance", "from": expected.value, "to": target.value})
            return AdvanceResult(True, target)

    async def record_failure(self, subject: str, code: str) -> None:
        async with self._lock:
            if subject != self.mapping.subject:
                return
            self.attempts += 1
            self.last_error = code
            self.audit_events.append({"action": "failure", "code": code})

    async def mark_session_closed(self, subject: str, *, closed: bool = True) -> None:
        async with self._lock:
            if subject == self.mapping.subject:
                self.legacy_session_closed = closed

    async def audit(self, subject: str, *, action: str, outcome: str, detail: dict[str, Any] | None = None) -> None:
        async with self._lock:
            if subject == self.mapping.subject:
                self.audit_events.append({"action": action, "outcome": outcome, "detail": detail or {}})


class FakeAuthentik:
    def __init__(self, *, ready: bool = False, active: bool = True, mfa_error: Exception | None = None) -> None:
        self.ready = ready
        self.active = active
        self.mfa_error = mfa_error
        self.password_calls: list[str] = []
        self.mfa_calls = 0
        self.closed = False

    async def find_user_by_subject(self, subject: str) -> AuthentikUser | None:
        if subject != SUBJECT:
            return None
        return AuthentikUser(
            pk=7,
            uuid=subject,
            uid=subject,
            username="user.one",
            name="Synthetic User",
            is_active=self.active,
        )

    async def set_password(self, subject: str, password: str) -> None:
        await asyncio.sleep(0)
        self.password_calls.append(password)

    async def mfa_ready(self, subject: str) -> bool:
        self.mfa_calls += 1
        if self.mfa_error is not None:
            raise self.mfa_error
        return self.ready

    async def revoke_sessions(self, subject: str) -> int:
        return 0

    async def aclose(self) -> None:
        self.closed = True


class FakeLegacySession:
    def __init__(
        self,
        *,
        user_id: str = USER_ID,
        account_id: str = ACCOUNT_ID,
        domain_id: str = DOMAIN_ID,
        mfa_enabled: bool = False,
        mfa_verified: bool = True,
        error: Exception | None = None,
    ) -> None:
        self.user_id = user_id
        self.account_id = account_id
        self.domain_id = domain_id
        self.session_key = "synthetic-session-key"
        self.mfa_enabled = mfa_enabled
        self.mfa_verified = mfa_verified
        self.error = error
        self.logout_called = False
        self.closed = False

    async def logout(self) -> None:
        self.logout_called = True

    async def aclose(self) -> None:
        self.closed = True


class FakeCloudStackLogin:
    def __init__(self, session: LegacySession | None = None, *, error: Exception | None = None) -> None:
        self.session = session
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def native_login(
        self,
        username: str,
        password: str,
        domain_id: str,
        otp: str | None = None,
    ) -> LegacySession:
        self.calls.append({"username": username, "password": password, "domain_id": domain_id, "otp": otp})
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        assert self.session is not None
        return self.session


class FakeProvisioner:
    def __init__(self, *, result: bool = True, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def authorize_saml_sso(self, *, user_id: str, entity_id: str, enable: bool = True) -> bool:
        self.calls.append({"user_id": user_id, "entity_id": entity_id, "enable": enable})
        if self.error is not None:
            raise self.error
        return self.result

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def store(mapping: IdentityMapping) -> FakeStore:
    return FakeStore(mapping)


@pytest.fixture
def authentik() -> FakeAuthentik:
    return FakeAuthentik()


@pytest.fixture
def legacy_session() -> FakeLegacySession:
    return FakeLegacySession()


@pytest.fixture
def cloudstack(legacy_session: FakeLegacySession) -> FakeCloudStackLogin:
    return FakeCloudStackLogin(legacy_session)


@pytest.fixture
def provisioner() -> FakeProvisioner:
    return FakeProvisioner()


@pytest.fixture
def service(
    store: FakeStore,
    authentik: FakeAuthentik,
    cloudstack: FakeCloudStackLogin,
    provisioner: FakeProvisioner,
    providers: DomainProviderMap,
    settings: IdentityBridgeSettings,
    when: datetime,
) -> IdentityMigrationService:
    return IdentityMigrationService(
        store=store,
        authentik=authentik,
        cloudstack_login=cloudstack,
        provisioner=provisioner,
        providers=providers,
        settings=settings,
        clock=lambda: when,
    )


@pytest.fixture
async def closed_service(service: IdentityMigrationService) -> AsyncIterator[IdentityMigrationService]:
    try:
        yield service
    finally:
        await service.close()


@pytest.fixture
def cloudstack_credentials() -> Any:
    from conductor.cloudstack.client import CloudStackCredentials

    return CloudStackCredentials(
        api_url="https://cloud.example.com/client/api",
        api_key="synthetic-provisioner-key",
        api_secret="synthetic-provisioner-secret",
    )


@pytest.fixture
def transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)


def legacy_password() -> str:
    return "Legacy-" + "Pass-1"


def synthetic_password() -> str:
    return "Synthetic-" + "Pass-1"


def other_password() -> str:
    return "A-Different-" + "Password"
