"""Router tests: mounting, CSRF fallback, TLS, rate limiting, redaction."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from conductor.errors import ConductorError
from conductor.identity.routes import build_router
from conductor.identity.service import IdentityMigrationService
from conductor.models.enums import MappingStage
from conftest import (
    SUBJECT,
    synthetic_password,  # type: ignore[import-not-found]
)


def _app(service: IdentityMigrationService, **kwargs) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ConductorError)
    async def _handler(request, exc: ConductorError):  # type: ignore[no-untyped-def]
        return JSONResponse(status_code=exc.status_code, content=exc.as_dict())

    app.include_router(build_router(service, subject_resolver=lambda request: SUBJECT, **kwargs))
    return app


async def _client(app: FastAPI, base_url: str = "https://testserver") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


async def test_get_migration_and_full_flow(service: IdentityMigrationService, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    app = _app(service)
    async with await _client(app) as client:
        response = await client.get("/api/v1/identity/migration")
        assert response.status_code == 200
        assert response.json()["stage"] == MappingStage.UNMIGRATED.value

        password = "Synthetic-Route-Password-9!"
        response = await client.post(
            "/api/v1/identity/migration/legacy-verify",
            json={"username": "user.one", "password": password, "otp": "123456"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == MappingStage.IDP_CREDENTIALS_READY.value
        assert password not in response.text
        assert password not in caplog.text

        response = await client.post("/api/v1/identity/migration/complete-mfa")
        assert response.status_code == 200
        response = await client.post("/api/v1/identity/migration/finish")
        assert response.status_code == 200
        assert response.json()["stage"] == MappingStage.COMPLETE.value
        assert "123456" not in caplog.text


async def test_default_csrf_guard_rejects_cross_origin(service: IdentityMigrationService) -> None:
    app = _app(service)
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/identity/migration/legacy-verify",
            json={"username": "user.one", "password": synthetic_password()},
            headers={"Origin": "https://evil.example.com"},
        )
        assert response.status_code == 403
        response = await client.post(
            "/api/v1/identity/migration/legacy-verify",
            json={"username": "user.one", "password": synthetic_password()},
            headers={"Origin": "https://testserver"},
        )
        assert response.status_code == 200, response.text


async def test_default_csrf_guard_rejects_form_posts(service: IdentityMigrationService) -> None:
    app = _app(service)
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/identity/migration/legacy-verify",
            data={"username": "user.one", "password": synthetic_password()},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "invalid_request"


async def test_tls_required(service: IdentityMigrationService) -> None:
    app = _app(service, require_tls=True)
    async with await _client(app, base_url="http://testserver") as client:
        response = await client.get("/api/v1/identity/migration")
        assert response.status_code == 400
        assert response.json()["code"] == "tls_required"


async def test_rate_limit(service: IdentityMigrationService) -> None:
    app = _app(service, rate_limit_per_minute=1)
    async with await _client(app) as client:
        assert (await client.get("/api/v1/identity/migration")).status_code == 200
        response = await client.get("/api/v1/identity/migration")
        assert response.status_code == 429


async def test_missing_subject_resolver_is_safe(service: IdentityMigrationService) -> None:
    app = FastAPI()

    @app.exception_handler(ConductorError)
    async def _handler(request, exc: ConductorError):  # type: ignore[no-untyped-def]
        return JSONResponse(status_code=exc.status_code, content=exc.as_dict())

    app.include_router(build_router(service, require_tls=False))
    async with await _client(app) as client:
        response = await client.get("/api/v1/identity/migration")
        assert response.status_code == 401


async def test_invalid_body_is_rejected_without_echo(service: IdentityMigrationService) -> None:
    app = _app(service)
    async with await _client(app) as client:
        response = await client.post(
            "/api/v1/identity/migration/legacy-verify",
            json={"username": "user.one", "password": ""},
        )
        assert response.status_code == 422
        assert "password" in response.text
