"""FastMCP server with authentik OIDC, per-client consent, and scoped tools."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.config import Settings
from conductor.errors import ConfigurationError
from conductor.execution.client import ExecutionClient
from conductor.mcp.storage import EncryptedFileStore
from conductor.mcp.tools import MCPToolService
from conductor.models.enums import Scope
from conductor.security.crypto import Vault

logger = logging.getLogger(__name__)

INSTRUCTIONS = """CloudStack Conductor exposes curated cloud operations for an authorized tenant.
Read tools return only the caller's authorized resources. Change tools prepare a plan;
the user must approve it in the portal before anything executes. No tool can approve its own
change, execute arbitrary CloudStack commands, run shell commands, or read another tenant's data.
Scopes: docs:read, cloud:read, pricing:read, usage:read, console:open, actions:propose, guest:propose.
"""


def create_mcp(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    execution: ExecutionClient,
    *,
    vault: Vault | None = None,
) -> FastMCP:
    if not settings.features.mcp_enabled:
        raise ConfigurationError("MCP is disabled in this deployment")
    if settings.auth.mode != "oidc":
        raise ConfigurationError("MCP requires OIDC authentication")
    if not settings.mcp_public_url:
        raise ConfigurationError("MCP requires an explicit public URL")
    if not settings.auth.oidc_issuer:
        raise ConfigurationError("MCP requires the OIDC issuer")
    vault = vault or Vault(settings.vault_key())
    storage_dir = Path(settings.mcp_storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(storage_dir, 0o700)
    except OSError:
        pass
    auth = OIDCProxy(
        config_url=settings.auth.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
        client_id=settings.auth.oidc_client_id or "",
        client_secret=settings.oidc_client_secret(),
        base_url=settings.mcp_public_url,
        resource_base_url=settings.mcp_public_url.rstrip("/") + "/mcp",
        issuer_url=settings.auth.oidc_issuer,
        redirect_path="/auth/callback",
        valid_scopes=[scope.value for scope in Scope],
        required_scopes=[Scope.DOCS_READ.value, Scope.CLOUD_READ.value],
        jwt_signing_key=settings.mcp_signing_key(),
        client_storage=EncryptedFileStore(storage_dir, vault),
        fastmcp_access_token_expiry_seconds=600,
        fallback_access_token_expiry_seconds=600,
        fallback_refresh_token_expiry_seconds=7 * 24 * 3600,
        require_authorization_consent=True,
        allowed_client_redirect_uris=settings.mcp_allowed_redirect_uris or None,
        enable_cimd=True,
    )
    mcp = FastMCP(
        name="CloudStack Conductor",
        instructions=INSTRUCTIONS,
        auth=auth,
    )
    service = MCPToolService(settings, session_factory, execution)

    @mcp.tool(name="docs_search", description="Search version-pinned public CloudStack documentation.")
    async def docs_search(query: str, limit: int = 8) -> dict[str, Any]:
        return await service.docs_search(query, limit=min(max(limit, 1), 20))

    @mcp.tool(name="docs_get", description="Read one indexed public documentation document.")
    async def docs_get(document_id: str) -> dict[str, Any]:
        return await service.docs_get(document_id)

    @mcp.tool(name="support_answer", description="Answer from cited public documentation.")
    async def support_answer(question: str) -> dict[str, Any]:
        return await service.support_answer(question)

    @mcp.tool(name="cloud_list_instances", description="List the caller's CloudStack instances.")
    async def cloud_list_instances() -> dict[str, Any]:
        return await service.cloud_list_instances()

    @mcp.tool(name="cloud_get_instance", description="Get one authorized instance by ID.")
    async def cloud_get_instance(instance_id: str) -> dict[str, Any]:
        return await service.cloud_get_instance(instance_id)

    @mcp.tool(name="cloud_list_volumes", description="List the caller's volumes.")
    async def cloud_list_volumes() -> dict[str, Any]:
        return await service.cloud_list_volumes()

    @mcp.tool(name="cloud_list_networks", description="List the caller's networks.")
    async def cloud_list_networks() -> dict[str, Any]:
        return await service.cloud_list_networks()

    @mcp.tool(name="cloud_list_offerings", description="List service offerings available to the caller.")
    async def cloud_list_offerings() -> dict[str, Any]:
        return await service.cloud_list_offerings()

    @mcp.tool(name="cloud_quote", description="Quote the caller's effective hourly price for an offering.")
    async def cloud_quote(offering_id: str) -> dict[str, Any]:
        return await service.cloud_quote(offering_id)

    @mcp.tool(
        name="cloud_get_usage_estimate",
        description="Current-state cost forecast for the caller's resources (provisional, not a bill).",
    )
    async def cloud_get_usage_estimate() -> dict[str, Any]:
        return await service.cloud_get_usage_estimate()

    @mcp.tool(
        name="cloud_prepare_action",
        description=(
            "Prepare a cloud change and return an approval URL. Nothing executes until the user "
            "approves it in the portal. Allowed actions are the documented typed operations; "
            "arbitrary API commands are rejected."
        ),
    )
    async def cloud_prepare_action(action: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        return await service.cloud_prepare_action(action, parameters)

    @mcp.tool(
        name="cloud_get_operation",
        description="Poll an operation by operation_id, or by plan_id after the user approves.",
    )
    async def cloud_get_operation(operation_id: str | None = None, plan_id: str | None = None) -> dict[str, Any]:
        return await service.cloud_get_operation(operation_id, plan_id)

    @mcp.tool(
        name="cloud_open_console",
        description="Create a single-use, user-bound console launch link.",
    )
    async def cloud_open_console(instance_id: str) -> dict[str, Any]:
        return await service.cloud_open_console(instance_id)

    @mcp.tool(
        name="guest_prepare_task",
        description="Prepare a reviewed managed-guest task (installation/inspection/expansion) for approval.",
    )
    async def guest_prepare_task(
        instance_id: str, task: str, parameters: dict[str, str] | None = None
    ) -> dict[str, Any]:
        return await service.guest_prepare_task(instance_id, task, parameters)

    return mcp
