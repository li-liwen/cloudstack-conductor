from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from conductor.config import Settings
from conductor.db.identity import IdentityRepository
from conductor.db.mcp import MCPRepository
from conductor.db.models import Base
from conductor.mcp.storage import EncryptedFileStore
from conductor.mcp.tools import MCPToolService
from conductor.models.enums import MappingStage, PrincipalRole, Scope
from conductor.models.principal import IdentityMapping
from conductor.security.crypto import Vault


class _FakeExecution:
    async def resources(self, *, subject: str, kind: str = "all") -> dict[str, Any]:
        return {
            "items": {
                "instances": [
                    {
                        "id": "11111111-1111-4111-8111-111111111111",
                        "name": "demo-web",
                        "state": "Running",
                        "ip_addresses": ["192.0.2.10"],
                    }
                ]
            }
        }

    async def quote(self, *, subject: str, parameters: dict[str, Any]) -> dict[str, Any]:
        return {"offering_id": parameters["offering_id"], "status": "priced"}

    async def usage_forecast(self, subject: str) -> dict[str, Any]:
        return {"total_hourly": "0.12", "completeness": "provisional"}

    async def console_lease(self, *, subject: str, vm_id: str) -> dict[str, Any]:
        return {"launch_url": "https://portal.example.com/console/launch/x", "vm_id": vm_id}

    async def create_plan(self, *, subject: str, action: str, parameters: dict[str, Any]) -> dict[str, Any]:
        return {
            "plan_id": "plan_1",
            "action": action,
            "digest": "d" * 64,
            "quote": {"total_hourly": "0.12", "currency": "CNY"},
            "effects": [],
            "state": "awaiting_approval",
            "expires_at": "2026-11-07T20:00:00Z",
        }

    async def get_operation(self, operation_id: str, subject: str) -> dict[str, Any]:
        return {"operation_id": operation_id, "state": "succeeded", "steps": [], "result": {}}

    async def plan_operation(self, plan_id: str, subject: str) -> dict[str, Any]:
        return {"operation_id": "op_1", "plan_id": plan_id, "state": "queued", "steps": []}


@pytest.fixture
async def mcp_env(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await IdentityRepository(session).upsert_mapping(
            IdentityMapping(
                subject="mcp-subject",
                cloud_id="cloud",
                cloudstack_user_id="user",
                cloudstack_account_id="account",
                cloudstack_domain_id="domain",
                cloudstack_domain="demo.example",
                cloudstack_username="demo-user",
                account="demo-account",
                domain_ancestors=["domain"],
                stage=MappingStage.COMPLETE,
                roles=[PrincipalRole.USER],
            )
        )
        await session.commit()
    settings = Settings(
        environment="demo",
        cloud_id="cloud",
        public_base_url="https://portal.example.com",
        auth={"mode": "demo", "allow_demo_auth": True},
        features={"console_enabled": False, "mcp_enabled": True},
        knowledge={"mock_embeddings": True},
    )
    service = MCPToolService(settings, factory, _FakeExecution())
    vault = Vault("mcp-test-vault-key-000000000000000000")
    store = EncryptedFileStore(tmp_path / "store", vault)
    yield SimpleNamespace(service=service, factory=factory, engine=engine, store=store, settings=settings)
    await engine.dispose()


def _token(scopes: list[str], *, subject: str = "mcp-subject"):
    return SimpleNamespace(
        token="synthetic-token",
        client_id="test-client",
        scopes=scopes,
        subject=subject,
        claims={"sub": subject},
        expires_at=None,
        resource=None,
        original_client_id=None,
    )


async def test_scope_is_enforced(mcp_env, monkeypatch) -> None:
    monkeypatch.setattr("conductor.mcp.tools.get_access_token", lambda: _token(["docs:read"]))
    with pytest.raises(ToolError):
        await mcp_env.service.cloud_list_instances()
    result = await mcp_env.service.docs_search("security")
    assert "data" in result


async def test_authorized_read_uses_tenant_resource(mcp_env, monkeypatch) -> None:
    monkeypatch.setattr("conductor.mcp.tools.get_access_token", lambda: _token([Scope.CLOUD_READ.value]))
    result = await mcp_env.service.cloud_get_instance("11111111-1111-4111-8111-111111111111")
    assert result["data"]["name"] == "demo-web"
    async with mcp_env.factory() as session:
        connections = await MCPRepository(session).list_for("mcp-subject")
    assert len(connections) == 1
    assert connections[0].scopes == [Scope.CLOUD_READ]


async def test_revoked_connection_is_blocked(mcp_env, monkeypatch) -> None:
    monkeypatch.setattr(
        "conductor.mcp.tools.get_access_token",
        lambda: _token([Scope.CLOUD_READ.value]),
    )
    await mcp_env.service.cloud_list_instances()
    async with mcp_env.factory() as session:
        connections = await MCPRepository(session).list_for("mcp-subject")
        await MCPRepository(session).revoke(connections[0].connection_id, principal_subject="mcp-subject")
        await session.commit()
    with pytest.raises(ToolError):
        await mcp_env.service.cloud_list_instances()


async def test_prepare_action_returns_approval_url(mcp_env, monkeypatch) -> None:
    monkeypatch.setattr("conductor.mcp.tools.get_access_token", lambda: _token([Scope.ACTIONS_PROPOSE.value]))
    result = await mcp_env.service.cloud_prepare_action("start_instance", {"instance_id": "x"})
    assert result["meta"]["approval_required"] is True
    assert result["data"]["approval_url"].startswith("https://portal.example.com/plans/")


async def test_encrypted_store_never_contains_plaintext(mcp_env, tmp_path) -> None:
    store = mcp_env.store
    await store.put("client-1", {"refresh_token": "synthetic-refresh-value"})
    value = await store.get("client-1")
    assert value == {"refresh_token": "synthetic-refresh-value"}
    files = list((tmp_path / "store").rglob("*"))
    contents = b"".join(path.read_bytes() for path in files if path.is_file())
    assert b"synthetic-refresh-value" not in contents
    assert await store.delete("client-1") is True
    assert await store.get("client-1") is None
