"""Service-level state-machine and security tests with injected doubles."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from conductor.errors import ConfigurationError
from conductor.identity.config import DomainProviderMap, DomainSamlProvider
from conductor.identity.errors import (
    IdentityMappingMismatch,
    IdentityNotProvisioned,
    IdentityProviderCapabilityError,
    LegacySecondFactorInvalid,
    LegacySecondFactorRequired,
    LegacySessionUnavailable,
    MfaEnrollmentRequired,
    MigrationStageConflict,
    SamlAuthorizationFailed,
    TlsRequired,
)
from conductor.identity.service import IdentityMigrationService
from conductor.identity.sessions import TemporarySessionRegistry
from conductor.models.enums import MappingStage, PrincipalRole
from conftest import (  # type: ignore[import-not-found]
    ENTITY_ID,
    SUBJECT,
    USER_ID,
    FakeAuthentik,
    FakeCloudStackLogin,
    FakeLegacySession,
    FakeProvisioner,
    FakeStore,
    legacy_password,
    other_password,
)


async def test_happy_path_reaches_complete(
    closed_service: IdentityMigrationService,
    store: FakeStore,
    authentik: FakeAuthentik,
    legacy_session: FakeLegacySession,
    provisioner: FakeProvisioner,
) -> None:
    status = await closed_service.legacy_verify(SUBJECT, username="user.one", password=legacy_password())
    assert status.stage is MappingStage.IDP_CREDENTIALS_READY
    assert store.mapping.stage is MappingStage.IDP_CREDENTIALS_READY
    assert authentik.password_calls == [legacy_password()]
    assert legacy_session.logout_called is True
    assert legacy_session.closed is True
    assert store.legacy_session_closed is True

    status = await closed_service.complete_mfa(SUBJECT)
    assert status.stage is MappingStage.REQUIRED_MFA_READY
    assert status.next_step == "finish"

    status = await closed_service.finish(SUBJECT)
    assert status.stage is MappingStage.COMPLETE
    assert status.next_step == "none"
    assert store.mapping.stage is MappingStage.COMPLETE
    assert provisioner.calls == [{"user_id": USER_ID, "entity_id": ENTITY_ID, "enable": True}]
    assert store.evidence_hash is not None
    assert len(store.evidence_hash) == 64
    assert all(character in "0123456789abcdef" for character in store.evidence_hash)
    assert legacy_password() not in json.dumps(store.audit_events)


async def test_status_requires_a_mapping(service: IdentityMigrationService) -> None:
    with pytest.raises(IdentityNotProvisioned):
        await service.status("another-subject")


async def test_username_mismatch_stops_before_cloudstack(
    closed_service: IdentityMigrationService,
    store: FakeStore,
    cloudstack: FakeCloudStackLogin,
    authentik: FakeAuthentik,
) -> None:
    with pytest.raises(IdentityMappingMismatch):
        await closed_service.legacy_verify(SUBJECT, username="user.two", password=legacy_password())
    assert cloudstack.calls == []
    assert authentik.password_calls == []
    assert store.stage is MappingStage.UNMIGRATED
    assert store.attempts == 1
    assert store.last_error == "identity_username_mismatch"


async def test_id_mismatch_rejected_before_password_write(
    closed_service: IdentityMigrationService, store: FakeStore, authentik: FakeAuthentik
) -> None:
    mismatched = FakeLegacySession(account_id="99999999-9999-4999-8999-999999999999")
    closed_service._cloudstack_login = FakeCloudStackLogin(mismatched)  # type: ignore[attr-defined]
    with pytest.raises(IdentityMappingMismatch):
        await closed_service.legacy_verify(SUBJECT, username="user.one", password=legacy_password())
    assert authentik.password_calls == []
    assert store.stage is MappingStage.UNMIGRATED
    assert store.last_error == "identity_mapping_mismatch"
    assert mismatched.logout_called is True


async def test_second_factor_required_when_cloudstack_reports_it(
    closed_service: IdentityMigrationService, store: FakeStore, authentik: FakeAuthentik
) -> None:
    session = FakeLegacySession(mfa_enabled=True, mfa_verified=False)
    closed_service._cloudstack_login = FakeCloudStackLogin(session)  # type: ignore[attr-defined]
    with pytest.raises(LegacySecondFactorRequired):
        await closed_service.legacy_verify(SUBJECT, username="user.one", password=legacy_password())
    assert authentik.password_calls == []
    assert store.stage is MappingStage.UNMIGRATED
    assert store.last_error == "legacy_second_factor_required"
    assert session.logout_called is True


async def test_invalid_second_factor_does_not_write_password(
    closed_service: IdentityMigrationService, store: FakeStore, authentik: FakeAuthentik
) -> None:
    closed_service._cloudstack_login = FakeCloudStackLogin(  # type: ignore[attr-defined]
        error=LegacySecondFactorInvalid()
    )
    with pytest.raises(LegacySecondFactorInvalid):
        await closed_service.legacy_verify(SUBJECT, username="user.one", password=legacy_password(), otp="000000")
    assert authentik.password_calls == []
    assert store.stage is MappingStage.UNMIGRATED
    assert store.last_error == "legacy_second_factor_invalid"


async def test_mfa_required_for_privileged_role(
    closed_service: IdentityMigrationService, store: FakeStore, authentik: FakeAuthentik
) -> None:
    store.mapping = store.mapping.model_copy(update={"roles": [PrincipalRole.DOMAIN_ADMIN]})
    store.stage = MappingStage.IDP_CREDENTIALS_READY
    store.mapping = store.mapping.model_copy(update={"stage": MappingStage.IDP_CREDENTIALS_READY})
    authentik.ready = False
    with pytest.raises(MfaEnrollmentRequired):
        await closed_service.complete_mfa(SUBJECT)
    assert store.stage is MappingStage.IDP_CREDENTIALS_READY
    assert store.last_error == "mfa_not_enrolled"
    assert authentik.mfa_calls == 1


async def test_mfa_ready_advances(
    closed_service: IdentityMigrationService, store: FakeStore, authentik: FakeAuthentik
) -> None:
    store.mapping = store.mapping.model_copy(update={"legacy_2fa": True})
    store.stage = MappingStage.IDP_CREDENTIALS_READY
    store.mapping = store.mapping.model_copy(update={"stage": MappingStage.IDP_CREDENTIALS_READY})
    authentik.ready = True
    status = await closed_service.complete_mfa(SUBJECT)
    assert status.stage is MappingStage.REQUIRED_MFA_READY
    assert status.required_mfa is True


async def test_optional_mfa_capability_error_does_not_block(
    closed_service: IdentityMigrationService, store: FakeStore, authentik: FakeAuthentik
) -> None:
    store.stage = MappingStage.IDP_CREDENTIALS_READY
    store.mapping = store.mapping.model_copy(update={"stage": MappingStage.IDP_CREDENTIALS_READY})
    authentik.mfa_error = IdentityProviderCapabilityError("no device api")
    status = await closed_service.complete_mfa(SUBJECT)
    assert status.stage is MappingStage.REQUIRED_MFA_READY
    assert store.last_error is None


async def test_repeated_verify_after_credentials_ready_is_idempotent(
    closed_service: IdentityMigrationService,
    store: FakeStore,
    authentik: FakeAuthentik,
    cloudstack: FakeCloudStackLogin,
) -> None:
    await closed_service.legacy_verify(SUBJECT, username="user.one", password=legacy_password())
    assert len(authentik.password_calls) == 1
    login_calls = len(cloudstack.calls)
    status = await closed_service.legacy_verify(SUBJECT, username="user.one", password=other_password())
    assert status.stage is MappingStage.IDP_CREDENTIALS_READY
    assert len(authentik.password_calls) == 1
    assert len(cloudstack.calls) == login_calls
    assert "A-Different-Password" not in json.dumps(store.audit_events)


async def test_interrupted_after_legacy_verified_recovers(
    closed_service: IdentityMigrationService, store: FakeStore, authentik: FakeAuthentik
) -> None:
    store.stage = MappingStage.LEGACY_CREDENTIALS_VERIFIED
    store.mapping = store.mapping.model_copy(update={"stage": MappingStage.LEGACY_CREDENTIALS_VERIFIED})
    status = await closed_service.legacy_verify(SUBJECT, username="user.one", password=legacy_password())
    assert status.stage is MappingStage.IDP_CREDENTIALS_READY
    assert authentik.password_calls == [legacy_password()]


async def test_concurrent_verify_sets_password_once(
    store: FakeStore,
    authentik: FakeAuthentik,
    providers: DomainProviderMap,
    settings,
) -> None:
    session = FakeLegacySession()
    service = IdentityMigrationService(
        store=store,
        authentik=authentik,
        cloudstack_login=FakeCloudStackLogin(session),
        provisioner=FakeProvisioner(),
        providers=providers,
        settings=settings,
    )
    results = await asyncio.gather(
        service.legacy_verify(SUBJECT, username="user.one", password=legacy_password()),
        service.legacy_verify(SUBJECT, username="user.one", password=legacy_password()),
    )
    assert all(result.stage is MappingStage.IDP_CREDENTIALS_READY for result in results)
    assert authentik.password_calls == [legacy_password()]
    assert store.stage is MappingStage.IDP_CREDENTIALS_READY


async def test_complete_flow_is_idempotent(
    closed_service: IdentityMigrationService,
    store: FakeStore,
    authentik: FakeAuthentik,
    provisioner: FakeProvisioner,
    cloudstack: FakeCloudStackLogin,
) -> None:
    await closed_service.legacy_verify(SUBJECT, username="user.one", password=legacy_password())
    await closed_service.complete_mfa(SUBJECT)
    await closed_service.finish(SUBJECT)
    password_calls = len(authentik.password_calls)
    login_calls = len(cloudstack.calls)
    provisioner_calls = len(provisioner.calls)

    assert (
        await closed_service.legacy_verify(SUBJECT, username="user.one", password="Other")
    ).stage is MappingStage.COMPLETE
    assert (await closed_service.complete_mfa(SUBJECT)).stage is MappingStage.COMPLETE
    assert (await closed_service.finish(SUBJECT)).stage is MappingStage.COMPLETE
    assert len(authentik.password_calls) == password_calls
    assert len(cloudstack.calls) == login_calls
    assert len(provisioner.calls) == provisioner_calls


async def test_finish_without_provider_entry_fails(
    store: FakeStore,
    authentik: FakeAuthentik,
    cloudstack: FakeCloudStackLogin,
    settings,
) -> None:
    store.stage = MappingStage.REQUIRED_MFA_READY
    store.mapping = store.mapping.model_copy(update={"stage": MappingStage.REQUIRED_MFA_READY})
    service = IdentityMigrationService(
        store=store,
        authentik=authentik,
        cloudstack_login=cloudstack,
        provisioner=FakeProvisioner(),
        providers=DomainProviderMap(
            domains={
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": DomainSamlProvider(
                    provider_id="other-provider",
                    entity_id="https://auth.example.com/application/saml/other/metadata/",
                )
            }
        ),
        settings=settings,
    )
    with pytest.raises(ConfigurationError):
        await service.finish(SUBJECT)


async def test_provisioner_failure_keeps_stage(closed_service: IdentityMigrationService, store: FakeStore) -> None:
    store.stage = MappingStage.REQUIRED_MFA_READY
    store.mapping = store.mapping.model_copy(update={"stage": MappingStage.REQUIRED_MFA_READY})
    closed_service._provisioner = FakeProvisioner(result=False)  # type: ignore[attr-defined]
    with pytest.raises(SamlAuthorizationFailed):
        await closed_service.finish(SUBJECT)
    assert store.stage is MappingStage.REQUIRED_MFA_READY
    assert store.last_error == "saml_authorization_failed"


async def test_retain_session_until_finish(
    store: FakeStore,
    authentik: FakeAuthentik,
    providers: DomainProviderMap,
    settings,
    provisioner: FakeProvisioner,
) -> None:
    session = FakeLegacySession()
    retained = settings.model_copy(update={"close_legacy_session_immediately": False})
    service = IdentityMigrationService(
        store=store,
        authentik=authentik,
        cloudstack_login=FakeCloudStackLogin(session),
        provisioner=provisioner,
        providers=providers,
        settings=retained,
        sessions=TemporarySessionRegistry(ttl_seconds=300),
    )
    await service.legacy_verify(SUBJECT, username="user.one", password=legacy_password())
    assert session.logout_called is False
    await service.complete_mfa(SUBJECT)
    status = await service.finish(SUBJECT)
    assert status.stage is MappingStage.COMPLETE
    assert session.logout_called is True
    assert session.closed is True


async def test_retain_session_missing_fails_closed(
    store: FakeStore, authentik: FakeAuthentik, providers: DomainProviderMap, settings
) -> None:
    store.stage = MappingStage.REQUIRED_MFA_READY
    store.mapping = store.mapping.model_copy(update={"stage": MappingStage.REQUIRED_MFA_READY})
    retained = settings.model_copy(update={"close_legacy_session_immediately": False})
    service = IdentityMigrationService(
        store=store,
        authentik=authentik,
        cloudstack_login=FakeCloudStackLogin(FakeLegacySession()),
        provisioner=FakeProvisioner(),
        providers=providers,
        settings=retained,
        sessions=TemporarySessionRegistry(ttl_seconds=300),
    )
    with pytest.raises(LegacySessionUnavailable):
        await service.finish(SUBJECT)
    assert store.stage is MappingStage.REQUIRED_MFA_READY
    assert store.last_error == "legacy_session_unavailable"


async def test_abort_never_moves_stage_backwards(
    closed_service: IdentityMigrationService, store: FakeStore, legacy_session: FakeLegacySession
) -> None:
    store.stage = MappingStage.IDP_CREDENTIALS_READY
    store.mapping = store.mapping.model_copy(update={"stage": MappingStage.IDP_CREDENTIALS_READY})
    status = await closed_service.abort(SUBJECT)
    assert status.stage is MappingStage.IDP_CREDENTIALS_READY
    assert store.attempts == 1
    assert store.last_error == "aborted_by_user"
    assert store.legacy_session_closed is True
    assert any(event["action"] == "identity.migration.abort" for event in store.audit_events)


async def test_abort_rejects_complete(closed_service: IdentityMigrationService, store: FakeStore) -> None:
    store.stage = MappingStage.COMPLETE
    store.mapping = store.mapping.model_copy(update={"stage": MappingStage.COMPLETE})
    with pytest.raises(MigrationStageConflict):
        await closed_service.abort(SUBJECT)


async def test_tls_is_required_before_any_work(closed_service: IdentityMigrationService, store: FakeStore) -> None:
    with pytest.raises(TlsRequired):
        await closed_service.legacy_verify(
            SUBJECT, username="user.one", password=legacy_password(), transport_secure=False
        )
    assert store.attempts == 0


async def test_unexpected_mfa_unknown_state_is_a_capability_error(
    closed_service: IdentityMigrationService, store: FakeStore
) -> None:
    closed_service._cloudstack_login = FakeCloudStackLogin(  # type: ignore[attr-defined]
        error=IdentityProviderCapabilityError("unknown 2fa")
    )
    with pytest.raises(IdentityProviderCapabilityError):
        await closed_service.legacy_verify(SUBJECT, username="user.one", password=legacy_password())
    assert store.last_error == "identity_provider_capability_missing"


async def test_logs_do_not_contain_password_or_otp(
    closed_service: IdentityMigrationService, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    password = "Synthetic-Legacy-Password-42!"
    otp = "654321"
    await closed_service.legacy_verify(SUBJECT, username="user.one", password=password, otp=otp)
    await closed_service.complete_mfa(SUBJECT)
    await closed_service.finish(SUBJECT)
    assert password not in caplog.text
    assert otp not in caplog.text
