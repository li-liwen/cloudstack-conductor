"""Runtime configuration, health, and metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from conductor.api.deps import APIServices, services
from conductor.version import __version__

router = APIRouter(tags=["system"])


@router.get("/api/v1/config")
async def public_config(services_bundle: APIServices = Depends(services)) -> dict:
    return services_bundle.settings.public_config()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "conductor-api", "version": __version__}


@router.get("/readyz")
async def readyz(request: Request, services_bundle: APIServices = Depends(services)) -> JSONResponse:
    from conductor.db.session import database_ready

    async with services_bundle.session_factory() as db:
        ready = await database_ready(db)
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "unavailable"},
    )


@router.get("/metrics")
async def metrics(services_bundle: APIServices = Depends(services)) -> Response:
    if not services_bundle.settings.metrics_enabled:
        return Response(status_code=404)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
