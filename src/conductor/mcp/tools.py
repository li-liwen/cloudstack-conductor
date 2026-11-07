"""Curated MCP tool surface.

Tools expose business operations only. There is no arbitrary CloudStack command,
SQL, filesystem access, shell, URL fetch, or credential argument. Every tool
checks the caller's scopes and the live connection state before returning data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor.config import Settings
from conductor.db.identity import IdentityRepository
from conductor.db.knowledge import KnowledgeRepository
from conductor.db.mcp import MCPRepository
from conductor.errors import ConductorError
from conductor.execution.client import ExecutionClient
from conductor.knowledge.search import KnowledgeSearch
from conductor.models.enums import MCPConnectionState, Scope
from conductor.models.principal import Principal
from conductor.security.principal import PrincipalService

logger = logging.getLogger(__name__)

_TOOL_ERROR = "The request could not be completed."


@dataclass
class ToolAccess:
    session: AsyncSession
    principal: Principal
    connection_id: str
    scopes: set[str]


class MCPToolService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        execution: ExecutionClient,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.execution = execution
        self.principals = PrincipalService(settings)

    async def _authorize(self, session: AsyncSession, required: Scope) -> ToolAccess:
        token = get_access_token()
        if token is None:
            raise ToolError("authentication is required")
        scopes = set(token.scopes or [])
        if required.value not in scopes:
            raise ToolError("the client has not been granted the required scope")
        subject = token.subject or str(token.claims.get("sub") or "")
        if not subject:
            raise ToolError("the access token has no subject")
        mapping = await IdentityRepository(session).get_mapping(subject)
        if mapping is None:
            raise ToolError("this identity is not provisioned for cloud access")
        principal = self.principals.build_principal(mapping)
        client_id = token.client_id or "unknown"
        client_name = str(token.claims.get("client_name") or token.claims.get("azp") or client_id)[:255]
        issued_at = None
        raw_iat = token.claims.get("iat")
        if isinstance(raw_iat, (int, float)):
            issued_at = datetime.fromtimestamp(float(raw_iat), tz=UTC)
        connection = await MCPRepository(session).record_connection(
            token_issued_at=issued_at,
            principal_subject=subject,
            cloud_id=principal.cloud_id,
            account_id=principal.cloudstack_account_id,
            client_id=client_id[:255],
            client_name=client_name,
            scopes=[Scope(scope) for scope in scopes if scope in {item.value for item in Scope}],
        )
        if connection.state is not MCPConnectionState.ACTIVE:
            raise ToolError("this MCP connection has been revoked")
        await MCPRepository(session).touch(connection.connection_id)
        await MCPRepository(session).append_audit(
            connection_id=connection.connection_id,
            event="authorized",
            tool_name=required.value,
            outcome="success",
        )
        return ToolAccess(
            session=session,
            principal=principal,
            connection_id=connection.connection_id,
            scopes=scopes,
        )

    async def _audit_failure(self, session: AsyncSession, connection_id: str, tool: str) -> None:
        try:
            await MCPRepository(session).append_audit(
                connection_id=connection_id, event="tool_call", tool_name=tool, outcome="failure"
            )
            await session.commit()
        except Exception:
            await session.rollback()

    async def _with_access(self, required: Scope, tool: str, fn) -> Any:
        async with self.session_factory() as session:
            try:
                access = await self._authorize(session, required)
            except ToolError:
                await session.rollback()
                raise
            try:
                result = await fn(access)
            except ConductorError as exc:
                await self._audit_failure(session, access.connection_id, tool)
                raise ToolError(exc.public_message) from exc
            except ToolError:
                await self._audit_failure(session, access.connection_id, tool)
                raise
            except Exception:
                logger.exception("MCP tool %s failed", tool)
                await self._audit_failure(session, access.connection_id, tool)
                raise ToolError(_TOOL_ERROR) from None
            await MCPRepository(session).append_audit(
                connection_id=access.connection_id,
                event="tool_call",
                tool_name=tool,
                outcome="success",
            )
            await session.commit()
            return result

    # -- documentation -----------------------------------------------------
    async def docs_search(self, query: str, limit: int = 8) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            response = await KnowledgeSearch(access.session, self.settings).search(query, limit=limit)
            return {
                "data": response.model_dump(mode="json"),
                "meta": {"retrieval": response.retrieval, "scope": "docs:read"},
            }

        return await self._with_access(Scope.DOCS_READ, "docs_search", _run)

    async def docs_get(self, document_id: str) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            document = await KnowledgeRepository(access.session).get_document(document_id)
            if document is None:
                raise ToolError("the document was not found")
            return {
                "data": document.model_dump(mode="json"),
                "meta": {"scope": "docs:read"},
            }

        return await self._with_access(Scope.DOCS_READ, "docs_get", _run)

    async def support_answer(self, question: str) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            response = await KnowledgeSearch(access.session, self.settings).search(question, limit=5)
            if not response.hits:
                return {
                    "data": {
                        "answer": "No public documentation matched this question.",
                        "citations": [],
                    },
                    "meta": {"retrieval": response.retrieval},
                }
            excerpts = "\n\n".join(
                f"[{index}] {hit.title}: {hit.snippet}" for index, hit in enumerate(response.hits, start=1)
            )
            answer = (
                "Based on the indexed public documentation:\n\n"
                + excerpts
                + "\n\nThis summary is limited to cited excerpts; verify version-specific details."
            )
            return {
                "data": {
                    "answer": answer,
                    "citations": [
                        {
                            "title": hit.title,
                            "source_url": hit.source_url,
                            "version": hit.version,
                            "section": hit.section,
                        }
                        for hit in response.hits
                    ],
                },
                "meta": {"retrieval": response.retrieval},
            }

        return await self._with_access(Scope.DOCS_READ, "support_answer", _run)

    # -- cloud reads -------------------------------------------------------
    async def cloud_list_instances(self) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            payload = await self.execution.resources(subject=access.principal.subject, kind="instance")
            return {"data": payload.get("items", {}).get("instances", [])}

        return await self._with_access(Scope.CLOUD_READ, "cloud_list_instances", _run)

    async def cloud_get_instance(self, instance_id: str) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            payload = await self.execution.resources(subject=access.principal.subject, kind="instance")
            items = payload.get("items", {}).get("instances", [])
            instance = next((item for item in items if item.get("id") == instance_id), None)
            if instance is None:
                raise ToolError("the instance was not found")
            return {"data": instance}

        return await self._with_access(Scope.CLOUD_READ, "cloud_get_instance", _run)

    async def cloud_list_volumes(self) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            payload = await self.execution.resources(subject=access.principal.subject, kind="volume")
            return {"data": payload.get("items", {}).get("volumes", [])}

        return await self._with_access(Scope.CLOUD_READ, "cloud_list_volumes", _run)

    async def cloud_list_networks(self) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            payload = await self.execution.resources(subject=access.principal.subject, kind="network")
            return {"data": payload.get("items", {}).get("networks", [])}

        return await self._with_access(Scope.CLOUD_READ, "cloud_list_networks", _run)

    async def cloud_list_offerings(self) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            payload = await self.execution.resources(subject=access.principal.subject, kind="offering")
            return {"data": payload.get("items", {}).get("offerings", [])}

        return await self._with_access(Scope.CLOUD_READ, "cloud_list_offerings", _run)

    async def cloud_quote(self, offering_id: str) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            quote = await self.execution.quote(
                subject=access.principal.subject, parameters={"offering_id": offering_id}
            )
            return {"data": quote, "meta": {"scope": "pricing:read"}}

        return await self._with_access(Scope.PRICING_READ, "cloud_quote", _run)

    async def cloud_get_usage_estimate(self) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            data = await self.execution.usage_forecast(access.principal.subject)
            return {"data": data, "meta": {"scope": "usage:read", "source": "current-state"}}

        return await self._with_access(Scope.USAGE_READ, "cloud_get_usage_estimate", _run)

    # -- actions -----------------------------------------------------------
    async def cloud_prepare_action(self, action: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            plan = await self.execution.create_plan(
                subject=access.principal.subject,
                action=action,
                parameters=parameters or {},
            )
            portal = (self.settings.portal_public_url or self.settings.public_base_url).rstrip("/")
            return {
                "data": {
                    "plan_id": plan["plan_id"],
                    "action": plan["action"],
                    "digest": plan["digest"],
                    "approval_url": f"{portal}/plans/{plan['plan_id']}",
                    "quote": plan.get("quote"),
                    "effects": plan.get("effects", []),
                    "downtime_seconds": plan.get("downtime_seconds"),
                    "data_effects": plan.get("data_effects", []),
                    "expires_at": plan.get("expires_at"),
                    "state": plan.get("state"),
                },
                "meta": {
                    "approval_required": True,
                    "approval_note": (
                        "The user must approve this plan in the portal. "
                        "Poll cloud_get_operation with plan_id after approval."
                    ),
                },
            }

        return await self._with_access(Scope.ACTIONS_PROPOSE, "cloud_prepare_action", _run)

    async def cloud_get_operation(self, operation_id: str | None = None, plan_id: str | None = None) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            if operation_id:
                operation = await self.execution.get_operation(operation_id, access.principal.subject)
            elif plan_id:
                operation = await self.execution.plan_operation(plan_id, access.principal.subject)
            else:
                raise ToolError("provide operation_id or plan_id")
            return {
                "data": {
                    "operation_id": operation.get("operation_id"),
                    "plan_id": operation.get("plan_id"),
                    "state": operation.get("state"),
                    "steps": operation.get("steps", []),
                    "result": operation.get("result", {}),
                    "error_code": operation.get("error_code"),
                    "updated_at": operation.get("updated_at"),
                }
            }

        return await self._with_access(Scope.CLOUD_READ, "cloud_get_operation", _run)

    async def cloud_open_console(self, instance_id: str) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            launch = await self.execution.console_lease(subject=access.principal.subject, vm_id=instance_id)
            return {
                "data": launch,
                "meta": {
                    "single_use": True,
                    "expires_seconds": self.settings.console.launch_ttl_seconds,
                },
            }

        return await self._with_access(Scope.CONSOLE_OPEN, "cloud_open_console", _run)

    async def guest_prepare_task(
        self,
        instance_id: str,
        task: str,
        parameters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        async def _run(access: ToolAccess) -> dict[str, Any]:
            plan = await self.execution.create_plan(
                subject=access.principal.subject,
                action="guest_task",
                parameters={
                    "instance_id": instance_id,
                    "task": task,
                    "parameters": parameters or {},
                },
            )
            portal = (self.settings.portal_public_url or self.settings.public_base_url).rstrip("/")
            return {
                "data": {
                    "plan_id": plan["plan_id"],
                    "digest": plan["digest"],
                    "approval_url": f"{portal}/plans/{plan['plan_id']}",
                    "effects": plan.get("effects", []),
                    "state": plan.get("state"),
                },
                "meta": {"approval_required": True},
            }

        return await self._with_access(Scope.GUEST_PROPOSE, "guest_prepare_task", _run)
