"""Execution-side CloudStack access: credentials, resource views, pricing.

Only this service decrypts user CloudStack keys. Agent workers, the portal API,
and the MCP service call the execution service instead.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from conductor.cloudstack.client import CloudStackClient, CloudStackCredentials
from conductor.cloudstack.models import (
    Network,
    ServiceOffering,
    Template,
    VirtualMachine,
    Volume,
    Zone,
)
from conductor.config import Settings
from conductor.db.identity import CredentialRepository
from conductor.db.pricing import PricingRepository
from conductor.errors import AuthorizationError, CloudStackError, ConfigurationError
from conductor.models.pricing import ComputeShape, PriceQuote
from conductor.models.principal import Principal
from conductor.policy.authorization import ensure_account_context
from conductor.security.crypto import Vault, credential_aad
from conductor.security.principal import PrincipalService


@dataclass(frozen=True)
class CredentialPayload:
    api_url: str
    api_key: str
    api_secret: str


class CredentialsProvider:
    def __init__(self, session: AsyncSession, vault: Vault) -> None:
        self.session = session
        self.vault = vault

    async def for_principal(self, principal: Principal) -> CloudStackCredentials:
        row = await CredentialRepository(self.session).get(principal.cloud_id, principal.cloudstack_user_id)
        if row is None:
            raise CloudStackError(
                "no CloudStack API credentials are provisioned for this identity; an administrator must provision them",
                error_code="credentials_missing",
            )
        if row.disabled:
            raise CloudStackError(
                "CloudStack API access is disabled for this identity",
                error_code="credentials_disabled",
            )
        try:
            payload = json.loads(
                self.vault.decrypt(
                    row.ciphertext,
                    aad=credential_aad(principal.cloud_id, principal.cloudstack_user_id),
                )
            )
            credentials = CredentialPayload(
                api_url=payload["api_url"],
                api_key=payload["api_key"],
                api_secret=payload["api_secret"],
            )
        except (KeyError, TypeError, ValueError, ConfigurationError) as exc:
            raise CloudStackError(
                "the stored CloudStack credential could not be used",
                error_code="credentials_invalid",
            ) from exc
        await CredentialRepository(self.session).mark_used(principal.cloud_id, principal.cloudstack_user_id)
        return CloudStackCredentials(
            api_url=credentials.api_url,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
        )


class CloudAccess:
    """One CloudStack client bound to one principal for one operation."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        vault: Vault,
        principal: Principal,
    ) -> None:
        self.session = session
        self.settings = settings
        self.vault = vault
        self.principal = principal
        self._credentials: CloudStackCredentials | None = None

    async def credentials(self) -> CloudStackCredentials:
        if self._credentials is None:
            self._credentials = await CredentialsProvider(self.session, self.vault).for_principal(self.principal)
        return self._credentials

    @asynccontextmanager
    async def client(self) -> AsyncIterator[CloudStackClient]:
        credentials = await self.credentials()
        client = CloudStackClient(
            credentials,
            timeout_seconds=60.0,
            max_concurrent=self.settings.limits.cloudstack_max_concurrent,
            rate_per_second=self.settings.limits.cloudstack_requests_per_second,
        )
        try:
            yield client
        finally:
            await client.close()

    def account_param(self, account_id: str | None) -> str | None:
        """Ask an administrative identity to select an account.

        Regular user credentials are implicitly scoped by CloudStack; passing
        ``accountid`` on their calls would be rejected or would widen scope.
        """

        if not account_id:
            return None
        if self.principal.is_operator:
            return account_id
        if account_id == self.principal.cloudstack_account_id:
            return None
        return account_id


class ResourceService:
    """Sanitized resource views. No raw API payloads reach the browser."""

    def __init__(self, access: CloudAccess) -> None:
        self.access = access

    def _account(self, account_id: str | None) -> str | None:
        ensure_account_context(self.access.principal, account_id)
        return self.access.account_param(account_id)

    async def list_instances(self, account_id: str | None = None) -> list[VirtualMachine]:
        async with self.access.client() as client:
            return await client.list_virtual_machines(account_id=self._account(account_id))

    async def list_volumes(self, account_id: str | None = None) -> list[Volume]:
        async with self.access.client() as client:
            return await client.list_volumes(account_id=self._account(account_id))

    async def list_networks(self, account_id: str | None = None) -> list[Network]:
        async with self.access.client() as client:
            return await client.list_networks(account_id=self._account(account_id))

    async def list_offerings(self) -> list[ServiceOffering]:
        async with self.access.client() as client:
            return await client.list_service_offerings()

    async def list_templates(self) -> list[Template]:
        async with self.access.client() as client:
            return await client.list_templates()

    async def list_zones(self) -> list[Zone]:
        async with self.access.client() as client:
            return await client.list_zones()

    async def get_vm(self, vm_id: str) -> VirtualMachine:
        async with self.access.client() as client:
            vm = await client.get_virtual_machine(vm_id)
        self._assert_owned(vm)
        return vm

    async def get_volume(self, volume_id: str) -> Volume:
        async with self.access.client() as client:
            volumes = await client.list_all("listVolumes", "volume", params={"id": volume_id})
        if not volumes:
            raise CloudStackError("the volume was not found", error_code="not_found")
        volume = Volume.model_validate(volumes[0])
        self._assert_owned(volume)
        return volume

    async def find_offering(self, offering_id: str) -> ServiceOffering | None:
        offerings = await self.list_offerings()
        return next((item for item in offerings if item.id == offering_id), None)

    async def find_template(self, template_id: str) -> Template | None:
        templates = await self.list_templates()
        return next((item for item in templates if item.id == template_id), None)

    async def find_network(self, network_id: str, account_id: str | None = None) -> Network | None:
        networks = await self.list_networks(account_id)
        return next((item for item in networks if item.id == network_id), None)

    async def find_zone(self, zone_id: str) -> Zone | None:
        zones = await self.list_zones()
        return next((item for item in zones if item.id == zone_id), None)

    def _assert_owned(self, resource: VirtualMachine | Volume | Network) -> None:
        principal = self.access.principal
        if principal.is_operator:
            return
        account_id = getattr(resource, "accountid", None)
        if account_id and account_id != principal.cloudstack_account_id:
            raise AuthorizationError("the resource belongs to another account")


def offering_shape(offering: ServiceOffering) -> ComputeShape:
    return ComputeShape(
        cpu_count=max(1, offering.cpunumber),
        memory_gib=offering.memory_gib if offering.memory_gib > 0 else Decimal(1),
        gpu_count=offering.gpunumber,
        name=offering.name,
    )


class PricingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def catalog(self, cloud_id: str):
        return await PricingRepository(self.session).load_catalog(cloud_id)

    async def quote_offering(
        self,
        principal: Principal,
        *,
        offering_id: str,
        offering_name: str,
        shape_fallback: ComputeShape | None,
        account_id: str | None = None,
        when: datetime | None = None,
        extra_ssd_gib: Decimal = Decimal(0),
        extra_hdd_gib: Decimal = Decimal(0),
    ) -> PriceQuote:
        catalog = await self.catalog(principal.cloud_id)
        return catalog.quote(
            cloud_id=principal.cloud_id,
            account_id=account_id or principal.cloudstack_account_id,
            offering_id=offering_id,
            offering_name=offering_name,
            shape_fallback=shape_fallback,
            when=when,
            domain_ancestors=principal.domain_ancestors,
            extra_ssd_gib=extra_ssd_gib,
            extra_hdd_gib=extra_hdd_gib,
        )

    async def quote_storage(
        self,
        principal: Principal,
        *,
        offering_id: str,
        offering_name: str,
        tier: str,
        gib: Decimal,
        account_id: str | None = None,
        when: datetime | None = None,
    ) -> PriceQuote:
        catalog = await self.catalog(principal.cloud_id)
        return catalog.storage_quote(
            cloud_id=principal.cloud_id,
            account_id=account_id or principal.cloudstack_account_id,
            offering_id=offering_id,
            offering_name=offering_name,
            tier=tier,
            gib=gib,
            when=when,
            domain_ancestors=principal.domain_ancestors,
        )


async def load_principal(session: AsyncSession, settings: Settings, subject: str) -> Principal:
    """Load a trusted principal from an internal service call.

    The caller has already authenticated the browser or MCP request; this
    method reconstructs the mapped principal without claims and never elevates
    roles based on caller-supplied strings.
    """

    repositories = PrincipalService(settings)
    # PrincipalService expects claims; use a small adapter that only reads the
    # mapping and preserves mapped roles.
    from conductor.db.identity import IdentityRepository

    repository = IdentityRepository(session)
    mapping = await repository.get_mapping(subject)
    if mapping is None:
        raise AuthorizationError("identity is not provisioned")
    return repositories.build_principal(mapping, mfa=mapping.mfa_required)
