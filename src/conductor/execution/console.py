"""Console broker.

The CloudStack console URL contains a short-lived native token. It is encrypted
at rest, never logged, and never sent to a browser. A one-time, user-bound
launch token is exchanged for a relay session whose upstream is validated
against an explicit host/port allowlist.
"""

from __future__ import annotations

import urllib.parse
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from conductor.config import Settings
from conductor.db.base import new_id, utcnow
from conductor.db.console import ConsoleRepository
from conductor.db.identity import AuditRepository
from conductor.errors import (
    AuthorizationError,
    CapabilityUnsupported,
    ConflictError,
    NotFoundError,
)
from conductor.execution.cloud import CloudAccess, load_principal
from conductor.models.console import ConsoleLaunch, ConsoleLease, ConsoleSession
from conductor.models.enums import ConsoleLeaseState
from conductor.security.crypto import Vault, console_aad, new_launch_token, token_hash


def _validate_console_url(url: str, settings: Settings) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"https", "wss"}:
        if settings.console.require_tls:
            raise CapabilityUnsupported("the console endpoint is not encrypted")
    if parsed.username or parsed.password:
        raise CapabilityUnsupported("the console endpoint contained credentials")
    host = (parsed.hostname or "").lower()
    if not host:
        raise CapabilityUnsupported("the console endpoint has no host")
    allowed = {item.lower() for item in settings.console.allowed_hosts}
    if host not in allowed:
        # An explicit loopback entry is allowed only when the operator listed
        # it. No implicit private-network fallback exists.
        raise CapabilityUnsupported("the console endpoint host is not allowlisted")
    try:
        port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
    except ValueError as exc:
        raise CapabilityUnsupported("the console endpoint port is invalid") from exc
    if port not in settings.console.allowed_ports:
        raise CapabilityUnsupported("the console endpoint port is not allowlisted")
    return parsed


class ConsoleBroker:
    def __init__(self, session: AsyncSession, settings: Settings, vault: Vault) -> None:
        self.session = session
        self.settings = settings
        self.vault = vault
        self.leases = ConsoleRepository(session)
        self.audit = AuditRepository(session)

    async def lease(self, *, subject: str, vm_id: str) -> ConsoleLaunch:
        if not self.settings.features.console_enabled:
            raise CapabilityUnsupported("the console is disabled")
        principal = await load_principal(self.session, self.settings, subject)
        access = CloudAccess(self.session, self.settings, self.vault, principal)
        async with access.client() as client:
            vm = await client.get_virtual_machine(vm_id)
        if not principal.is_operator and vm.accountid and vm.accountid != principal.cloudstack_account_id:
            raise AuthorizationError("the instance belongs to another account")
        async with access.client() as client:
            endpoint = await client.create_console_endpoint(vm.id)
        _validate_console_url(endpoint.url, self.settings)
        now = utcnow()
        lease_id = new_id("lease")
        token = new_launch_token()
        expiry = now + timedelta(seconds=self.settings.console.launch_ttl_seconds)
        session_id = new_id("csess")
        lease = ConsoleLease(
            lease_id=lease_id,
            vm_id=vm.id,
            vm_name=vm.name,
            principal_subject=subject,
            tenant_key=principal.tenant_key,
            account_id=principal.cloudstack_account_id,
            state=ConsoleLeaseState.ISSUED,
            created_at=now,
            expires_at=expiry,
            launch_token_hash=token_hash(token),
            session_id=session_id,
        )
        ciphertext = self.vault.encrypt(endpoint.url, aad=console_aad(lease_id))
        await self.leases.create(
            lease,
            endpoint_ciphertext=ciphertext,
            endpoint_key_id=self.vault.key_id,
            endpoint_expires_at=expiry + timedelta(seconds=self.settings.console.session_ttl_seconds),
        )
        await self.audit.append(
            actor_subject=subject,
            action="console.lease",
            target_type="instance",
            target_id=vm.id,
            outcome="success",
            detail={"lease_id": lease_id},
        )
        launch_url = f"{self.settings.public_base_url.rstrip('/')}/api/v1/console/launch/{lease_id}?token={token}"
        return ConsoleLaunch(
            launch_url=launch_url,
            expires_at=expiry,
            vm_id=vm.id,
            vm_name=vm.name,
        )

    async def exchange(self, lease_id: str, token: str, *, subject: str) -> ConsoleSession:
        current = await self.leases.get_for_update(lease_id)
        if current is None:
            raise NotFoundError("the console lease was not found")
        if current.principal_subject != subject:
            raise AuthorizationError("the console lease belongs to another principal")
        if current.state is not ConsoleLeaseState.ISSUED:
            raise ConflictError("the console launch was already used")
        if current.launch_token_hash != token_hash(token):
            raise AuthorizationError("the console launch token is not valid")
        now = utcnow()
        if current.expires_at <= now:
            await self.leases.mark_expired(lease_id)
            await self.session.commit()
            raise ConflictError("the console launch expired; open the console again")
        if not current.session_id:
            raise CapabilityUnsupported("the console session was not prepared")
        await self.leases.consume(lease_id, session_id=current.session_id)
        await self.audit.append(
            actor_subject=current.principal_subject,
            action="console.launched",
            target_type="instance",
            target_id=current.vm_id,
            outcome="success",
            detail={"lease_id": lease_id},
        )
        await self.session.commit()
        return ConsoleSession(
            session_id=current.session_id,
            vm_id=current.vm_id,
            ws_path=f"/api/v1/console/ws/{current.session_id}",
            expires_at=now + timedelta(seconds=self.settings.console.session_ttl_seconds),
        )

    async def endpoint_for_session(self, session_id: str) -> tuple[ConsoleLease, str]:
        from sqlalchemy import select

        from conductor.db.models import ConsoleLeaseRow

        result = await self.session.execute(
            select(ConsoleLeaseRow).where(
                ConsoleLeaseRow.session_id == session_id,
                ConsoleLeaseRow.state == ConsoleLeaseState.CONSUMED.value,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError("the console session was not found")
        now = utcnow()
        if row.revoked_at is not None:
            raise AuthorizationError("the console session was revoked")
        if row.consumed_at and row.consumed_at + timedelta(seconds=self.settings.console.session_ttl_seconds) <= now:
            raise AuthorizationError("the console session expired")
        if not row.endpoint_ciphertext:
            raise CapabilityUnsupported("the console endpoint is unavailable")
        endpoint = self.vault.decrypt(row.endpoint_ciphertext, aad=console_aad(row.lease_id))
        _validate_console_url(endpoint, self.settings)
        lease = ConsoleLease(
            lease_id=row.lease_id,
            vm_id=row.vm_id,
            vm_name=row.vm_name,
            principal_subject=row.principal_subject,
            tenant_key=row.tenant_key,
            account_id=row.account_id,
            state=ConsoleLeaseState(row.state),
            created_at=row.created_at,
            expires_at=row.expires_at,
            consumed_at=row.consumed_at,
            revoked_at=row.revoked_at,
            launch_token_hash=row.launch_token_hash,
            session_id=row.session_id,
        )
        return lease, endpoint


def parse_console_target(url: str) -> tuple[str, int, str]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return host, port, path
