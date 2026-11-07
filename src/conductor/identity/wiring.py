"""Composition helper for the portal API integration owner.

The portal's ``APIServices`` bundle is intentionally untouched by this package.
An integration can build the bridge at application startup, keep it on
``app.state``, mount :func:`conductor.identity.routes.build_router`, and close
the service during shutdown.
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.identity.authentik import AuthentikAdminAdapter
from conductor.identity.cloudstack_login import CloudStackNativeLoginAdapter
from conductor.identity.config import IdentityBridgeSettings
from conductor.identity.provisioner import CloudStackSamlProvisioner
from conductor.identity.service import IdentityMigrationService
from conductor.identity.sessions import TemporarySessionRegistry
from conductor.identity.store import SQLIdentityMigrationStore


def build_identity_service(
    settings: IdentityBridgeSettings,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> IdentityMigrationService:
    """Validate private inputs and construct all production adapters."""

    settings.validate_for_runtime()
    providers = settings.domain_providers()
    authentik = AuthentikAdminAdapter(
        base_url=settings.authentik_base_url or "",
        api_prefix=settings.authentik_api_prefix,
        token=settings.service_token(),
        timeout_seconds=settings.request_timeout_seconds,
        transport=transport,
    )
    login = CloudStackNativeLoginAdapter(
        api_url=settings.cloudstack_api_url or "",
        timeout_seconds=settings.request_timeout_seconds,
        transport=transport,
    )
    provisioner = CloudStackSamlProvisioner(
        settings.provisioner_credentials(),
        timeout_seconds=settings.request_timeout_seconds,
        transport=transport,
    )
    sessions = TemporarySessionRegistry(ttl_seconds=settings.legacy_session_ttl_seconds)
    return IdentityMigrationService(
        store=SQLIdentityMigrationStore(session_factory),
        authentik=authentik,
        cloudstack_login=login,
        provisioner=provisioner,
        providers=providers,
        settings=settings,
        sessions=sessions,
    )
