"""API authentication and CSRF integration tests (PostgreSQL required)."""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from conductor.api.app import create_app
from conductor.config import Settings
from conductor.db.identity import IdentityRepository
from conductor.db.models import Base
from conductor.db.session import build_engine, build_session_factory
from conductor.models.enums import MappingStage, PrincipalRole
from conductor.models.principal import IdentityMapping

DATABASE_URL = os.environ.get("CONDUCTOR_TEST_DATABASE_URL")
pytestmark = pytest.mark.requires_db


@pytest.fixture
def client():
    if not DATABASE_URL:
        pytest.skip("CONDUCTOR_TEST_DATABASE_URL is not set")
    os.environ.setdefault("CONDUCTOR_SESSION_SECRET", "integration-session-signing-value")
    os.environ.setdefault("CONDUCTOR_INTERNAL_TOKEN", "integration-internal-service-token")
    settings = Settings(
        environment="demo",
        cloud_id="demo-cloud",
        public_base_url="http://testserver",
        database={"url": DATABASE_URL},
        auth={
            "mode": "demo",
            "allow_demo_auth": True,
            "secure_cookies": False,
            "session_secret_file": None,
        },
        features={"console_enabled": False, "mcp_enabled": False},
        knowledge={"mock_embeddings": True},
    )

    async def seed() -> None:
        engine = build_engine(settings)
        async with engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.run_sync(Base.metadata.create_all)
        factory = build_session_factory(engine)
        async with factory() as session:
            await IdentityRepository(session).upsert_mapping(
                IdentityMapping(
                    subject="demo-user",
                    cloud_id="demo-cloud",
                    cloudstack_user_id="33333333-3333-4333-8333-333333333333",
                    cloudstack_account_id="22222222-2222-4222-8222-222222222222",
                    cloudstack_domain_id="11111111-1111-4111-8111-111111111111",
                    cloudstack_domain="demo.example",
                    cloudstack_username="demo-user",
                    account="aurora-lab",
                    domain_ancestors=["11111111-1111-4111-8111-111111111111"],
                    stage=MappingStage.COMPLETE,
                    roles=[PrincipalRole.USER],
                )
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(seed())
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def test_demo_login_session_and_csrf(client: TestClient) -> None:
    config = client.get("/api/v1/config")
    assert config.status_code == 200
    assert "vault" not in config.text.lower()
    login = client.post("/api/v1/auth/demo-login", json={"subject": "demo-user"})
    assert login.status_code == 200, login.text
    csrf = client.cookies.get("conductor_csrf")
    assert csrf
    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["principal"]["subject"] == "demo-user"
    # CSRF is mandatory for browser mutations.
    denied = client.post("/api/v1/conversations", json={"title": "Demo"})
    assert denied.status_code == 403
    allowed = client.post(
        "/api/v1/conversations",
        json={"title": "Demo"},
        headers={"X-CSRF-Token": csrf},
    )
    assert allowed.status_code == 200, allowed.text
    conversation_id = allowed.json()["conversation_id"]
    denied_message = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"message": "hello", "locale": "en"},
    )
    assert denied_message.status_code == 403


def test_unauthenticated_access_is_rejected(client: TestClient) -> None:
    assert client.get("/api/v1/auth/session").status_code == 401
    assert client.get("/api/v1/conversations").status_code == 401


def test_security_headers(client: TestClient) -> None:
    response = client.get("/api/v1/config")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"


def test_identity_router_is_not_shadowed_by_spa(monkeypatch) -> None:
    """The portal catch-all must be mounted after startup-added routers."""

    if not DATABASE_URL:
        pytest.skip("CONDUCTOR_TEST_DATABASE_URL is not set")
    monkeypatch.setenv("CONDUCTOR_IDENTITY__ENABLED", "true")
    monkeypatch.setenv("CONDUCTOR_SESSION_SECRET", "integration-session-signing-value")
    monkeypatch.setenv("CONDUCTOR_INTERNAL_TOKEN", "integration-internal-service-token")

    class _FakeIdentityService:
        require_tls = False

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        "conductor.api.app.build_identity_service",
        lambda *args, **kwargs: _FakeIdentityService(),
    )
    settings = Settings(
        environment="demo",
        cloud_id="demo-cloud",
        public_base_url="http://testserver",
        database={"url": DATABASE_URL},
        auth={"mode": "demo", "allow_demo_auth": True, "secure_cookies": False, "session_secret_file": None},
        features={"console_enabled": False, "mcp_enabled": False},
        knowledge={"mock_embeddings": True},
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/identity/migration")
    assert response.status_code == 401, response.text
