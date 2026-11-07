"""Password-migration orchestration.

The service is the only place that understands the complete first-login
sequence. It receives adapters through protocols, so the state-machine tests do
not need PostgreSQL, authentik, or CloudStack.

Security invariants:

* the password and OTP are local variables for one request only;
* the CloudStack login credential is the user's own legacy password, never the
  provisioner API key;
* a mismatch between the login response and the pre-provisioned mapping stops
  the flow before any password is written;
* every forward transition is a compare-and-set under a database row lock;
* repeated requests at or beyond a completed stage are idempotent and never
  reset a password;
* audit rows and evidence hashes contain no secret material.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from conductor.errors import ConductorError, ConfigurationError
from conductor.identity.config import DomainProviderMap, DomainSamlProvider, IdentityBridgeSettings
from conductor.identity.errors import (
    IdentityMappingMismatch,
    IdentityNotProvisioned,
    IdentityProviderError,
    LegacyCredentialsInvalid,
    LegacySecondFactorRequired,
    LegacySessionUnavailable,
    MfaEnrollmentRequired,
    MigrationStageConflict,
    SamlAuthorizationFailed,
    TlsRequired,
)
from conductor.identity.evidence import transition_evidence
from conductor.identity.models import MigrationState, MigrationStatus, NextStep
from conductor.identity.protocols import (
    AuthentikAdmin,
    CloudStackLogin,
    LegacySession,
    MigrationStore,
    SamlProvisioner,
    SessionRegistry,
)
from conductor.identity.sessions import TemporarySessionRegistry
from conductor.identity.stages import is_at_least, next_step, required_mfa
from conductor.models.enums import MappingStage

logger = logging.getLogger(__name__)


class IdentityMigrationService:
    def __init__(
        self,
        *,
        store: MigrationStore,
        authentik: AuthentikAdmin,
        cloudstack_login: CloudStackLogin,
        provisioner: SamlProvisioner | None,
        providers: DomainProviderMap | None,
        settings: IdentityBridgeSettings,
        sessions: SessionRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._authentik = authentik
        self._cloudstack_login = cloudstack_login
        self._provisioner = provisioner
        self._providers = providers
        self._settings = settings
        self._sessions = sessions or TemporarySessionRegistry(ttl_seconds=settings.legacy_session_ttl_seconds)
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    @property
    def require_tls(self) -> bool:
        return self._settings.require_tls

    async def status(self, subject: str) -> MigrationStatus:
        return self._status(await self._load(subject))

    async def legacy_verify(
        self,
        subject: str,
        *,
        username: str,
        password: str,
        otp: str | None = None,
        transport_secure: bool = True,
    ) -> MigrationStatus:
        lock = await self._subject_lock(subject)
        async with lock:
            return await self._legacy_verify(
                subject,
                username=username,
                password=password,
                otp=otp,
                transport_secure=transport_secure,
            )

    async def _legacy_verify(
        self,
        subject: str,
        *,
        username: str,
        password: str,
        otp: str | None = None,
        transport_secure: bool = True,
    ) -> MigrationStatus:
        self._check_transport(transport_secure)
        state = await self._load(subject)
        if is_at_least(state.stage, MappingStage.IDP_CREDENTIALS_READY):
            # The credential bridge already succeeded. Do not touch authentik
            # again, even if the client retries with a different password.
            return self._status(state)
        if state.stage not in {MappingStage.UNMIGRATED, MappingStage.LEGACY_CREDENTIALS_VERIFIED}:
            raise MigrationStageConflict()
        if not password:
            await self._fail(subject, "legacy_credentials_incomplete")
            raise LegacyCredentialsInvalid()
        if username != state.mapping.cloudstack_username:
            await self._fail(subject, "identity_username_mismatch")
            raise IdentityMappingMismatch("the supplied username does not match the provisioned identity")

        session: LegacySession | None = None
        try:
            session = await self._cloudstack_login.native_login(
                username=username,
                password=password,
                domain_id=state.mapping.cloudstack_domain_id,
                otp=otp,
            )
            self._assert_mapping(state, session)
            user = await self._authentik.find_user_by_subject(subject)
            if user is None or not user.is_active:
                raise IdentityNotProvisioned()
            if session.mfa_enabled and not session.mfa_verified:
                raise LegacySecondFactorRequired()

            if state.stage is MappingStage.UNMIGRATED:
                result = await self._store.advance(
                    subject,
                    MappingStage.UNMIGRATED,
                    MappingStage.LEGACY_CREDENTIALS_VERIFIED,
                    evidence_hash=self._evidence(
                        subject,
                        "legacy_credentials_verified",
                        MappingStage.UNMIGRATED,
                        MappingStage.LEGACY_CREDENTIALS_VERIFIED,
                    ),
                )
                if not result.ok:
                    await self._close_session(session)
                    session = None
                    fresh = await self._load(subject)
                    return self._status(fresh)

            await self._authentik.set_password(subject, password)
            password = ""
            result = await self._store.advance(
                subject,
                MappingStage.LEGACY_CREDENTIALS_VERIFIED,
                MappingStage.IDP_CREDENTIALS_READY,
                evidence_hash=self._evidence(
                    subject,
                    "idp_credentials_ready",
                    MappingStage.LEGACY_CREDENTIALS_VERIFIED,
                    MappingStage.IDP_CREDENTIALS_READY,
                ),
            )
            if not result.ok:
                # A concurrent request won the transition. It used the same
                # legacy credential, so there is nothing to reset.
                await self._close_session(session)
                session = None
                fresh = await self._load(subject)
                return self._status(fresh)

            if self._settings.close_legacy_session_immediately:
                await self._close_session(session)
                session = None
                await self._store.mark_session_closed(subject, closed=True)
            else:
                await self._sessions.put(subject, session)
                session = None
            fresh = await self._load(subject)
            return self._status(fresh)
        except ConductorError as exc:
            await self._fail(subject, exc.code)
            raise
        except Exception as exc:  # pragma: no cover - defensive boundary
            logger.exception("identity migration failed")
            await self._fail(subject, "identity_bridge_error")
            raise IdentityProviderError("the identity migration could not be completed") from exc
        finally:
            if session is not None:
                await self._close_session(session)

    async def complete_mfa(self, subject: str) -> MigrationStatus:
        lock = await self._subject_lock(subject)
        async with lock:
            return await self._complete_mfa(subject)

    async def _complete_mfa(self, subject: str) -> MigrationStatus:
        state = await self._load(subject)
        if is_at_least(state.stage, MappingStage.REQUIRED_MFA_READY):
            return self._status(state)
        if state.stage is not MappingStage.IDP_CREDENTIALS_READY:
            raise MigrationStageConflict()
        required = required_mfa(state.mapping)
        ready = False
        try:
            ready = await self._authentik.mfa_ready(subject)
        except ConductorError as exc:
            if required:
                await self._fail(subject, exc.code)
                raise
            # MFA is optional for this identity; an older authentik that lacks
            # the device API must not block the migration.
            logger.info("authentik MFA state is unavailable; optional MFA is not enforced")
        if required and not ready:
            await self._fail(subject, "mfa_not_enrolled")
            raise MfaEnrollmentRequired()
        result = await self._store.advance(
            subject,
            MappingStage.IDP_CREDENTIALS_READY,
            MappingStage.REQUIRED_MFA_READY,
            evidence_hash=self._evidence(
                subject,
                "required_mfa_ready",
                MappingStage.IDP_CREDENTIALS_READY,
                MappingStage.REQUIRED_MFA_READY,
                extra={"required_mfa": required, "mfa_ready": ready},
            ),
        )
        if not result.ok:
            return self._status(await self._load(subject))
        return self._status(await self._load(subject))

    async def finish(self, subject: str) -> MigrationStatus:
        lock = await self._subject_lock(subject)
        async with lock:
            return await self._finish(subject)

    async def _finish(self, subject: str) -> MigrationStatus:
        state = await self._load(subject)
        if state.stage is MappingStage.COMPLETE:
            return self._status(state)
        if not is_at_least(state.stage, MappingStage.REQUIRED_MFA_READY):
            raise MigrationStageConflict()
        provider = self._require_provider(state)
        self._check_provider_binding(state, provider)
        if (
            not self._settings.close_legacy_session_immediately
            and not state.legacy_session_closed
            and await self._sessions.peek(subject) is None
        ):
            await self._fail(subject, "legacy_session_unavailable")
            raise LegacySessionUnavailable()

        if state.stage is MappingStage.REQUIRED_MFA_READY:
            if self._provisioner is None:
                raise ConfigurationError("the CloudStack SAML provisioner is not configured")
            try:
                accepted = await self._provisioner.authorize_saml_sso(
                    user_id=state.mapping.cloudstack_user_id,
                    entity_id=provider.entity_id,
                    enable=True,
                )
                if not accepted:
                    raise SamlAuthorizationFailed()
            except ConductorError as exc:
                await self._fail(subject, exc.code)
                raise
            result = await self._store.advance(
                subject,
                MappingStage.REQUIRED_MFA_READY,
                MappingStage.CLOUDSTACK_SAML_AUTHORIZED,
                saml_provider_id=provider.provider_id,
                evidence_hash=self._evidence(
                    subject,
                    "cloudstack_saml_authorized",
                    MappingStage.REQUIRED_MFA_READY,
                    MappingStage.CLOUDSTACK_SAML_AUTHORIZED,
                    provider_id=provider.provider_id,
                ),
            )
            if not result.ok:
                state = await self._load(subject)
                if not is_at_least(state.stage, MappingStage.CLOUDSTACK_SAML_AUTHORIZED):
                    return self._status(state)

        state = await self._load(subject)
        if state.stage is MappingStage.CLOUDSTACK_SAML_AUTHORIZED:
            closed = await self._close_pending_session(subject, state)
            result = await self._store.advance(
                subject,
                MappingStage.CLOUDSTACK_SAML_AUTHORIZED,
                MappingStage.COMPLETE,
                saml_provider_id=provider.provider_id,
                legacy_session_closed=closed,
                evidence_hash=self._evidence(
                    subject,
                    "complete",
                    MappingStage.CLOUDSTACK_SAML_AUTHORIZED,
                    MappingStage.COMPLETE,
                    provider_id=provider.provider_id,
                ),
            )
            if not result.ok:
                return self._status(await self._load(subject))
        return self._status(await self._load(subject))

    async def abort(self, subject: str) -> MigrationStatus:
        lock = await self._subject_lock(subject)
        async with lock:
            return await self._abort(subject)

    async def _abort(self, subject: str) -> MigrationStatus:
        state = await self._load(subject)
        if state.stage is MappingStage.COMPLETE:
            raise MigrationStageConflict()
        session = await self._sessions.pop(subject)
        if session is not None:
            await self._close_session(session)
        closed = True if session is not None else state.legacy_session_closed
        if self._settings.close_legacy_session_immediately:
            closed = True
        await self._store.mark_session_closed(subject, closed=closed)
        await self._store.record_failure(subject, "aborted_by_user")
        await self._store.audit(
            subject,
            action="identity.migration.abort",
            outcome="aborted",
            detail={"stage": state.stage.value},
        )
        return self._status(await self._load(subject))

    async def close(self) -> None:
        await self._sessions.close_all()
        await self._authentik.aclose()
        if self._provisioner is not None:
            await self._provisioner.aclose()

    # -- helpers -----------------------------------------------------------
    async def _subject_lock(self, subject: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(subject)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[subject] = lock
            return lock

    async def _load(self, subject: str) -> MigrationState:
        state = await self._store.load(subject)
        if state is None:
            raise IdentityNotProvisioned()
        return state

    def _status(self, state: MigrationState) -> MigrationStatus:
        provider = self._provider_for(state.mapping.cloudstack_domain_id)
        return MigrationStatus(
            stage=state.stage,
            next_step=cast(NextStep, next_step(state.stage)),
            required_mfa=required_mfa(state.mapping),
            saml_provider_configured=provider is not None,
            attempts=state.attempts,
            last_error_code=state.last_error,
            legacy_session_closed=state.legacy_session_closed,
            updated_at=state.updated_at,
        )

    @staticmethod
    def _assert_mapping(state: MigrationState, session: LegacySession) -> None:
        mapping = state.mapping
        if (
            session.user_id != mapping.cloudstack_user_id
            or session.account_id != mapping.cloudstack_account_id
            or session.domain_id != mapping.cloudstack_domain_id
        ):
            raise IdentityMappingMismatch()

    def _provider_for(self, domain_id: str) -> DomainSamlProvider | None:
        if self._providers is None:
            return None
        return self._providers.for_domain(domain_id)

    def _require_provider(self, state: MigrationState) -> DomainSamlProvider:
        provider = self._provider_for(state.mapping.cloudstack_domain_id)
        if provider is None:
            raise ConfigurationError("the CloudStack domain SAML provider map has no entry for this identity")
        return provider

    @staticmethod
    def _check_provider_binding(state: MigrationState, provider: DomainSamlProvider) -> None:
        configured = state.mapping.saml_provider_id
        if configured and configured not in {provider.provider_id, provider.entity_id}:
            raise MigrationStageConflict("the identity is bound to a different SAML provider")

    async def _close_pending_session(self, subject: str, state: MigrationState) -> bool:
        session = await self._sessions.pop(subject)
        if session is not None:
            await self._close_session(session)
            return True
        if state.legacy_session_closed or self._settings.close_legacy_session_immediately:
            return True
        await self._fail(subject, "legacy_session_unavailable")
        raise LegacySessionUnavailable()

    async def _close_session(self, session: LegacySession) -> None:
        try:
            await session.logout()
        except Exception:
            logger.info("temporary CloudStack session logout did not complete")
        try:
            await session.aclose()
        except Exception:
            logger.info("temporary CloudStack session could not be closed")

    async def _fail(self, subject: str, code: str) -> None:
        try:
            await self._store.record_failure(subject, code)
        except Exception:
            logger.exception("could not record an identity migration failure")

    def _evidence(
        self,
        subject: str,
        action: str,
        from_stage: MappingStage,
        to_stage: MappingStage,
        *,
        provider_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> str:
        return transition_evidence(
            subject=subject,
            action=action,
            from_stage=from_stage,
            to_stage=to_stage,
            provider_id=provider_id,
            at=self._clock(),
            extra=extra,
        )

    def _check_transport(self, transport_secure: bool) -> None:
        if self._settings.require_tls and not transport_secure:
            raise TlsRequired()
