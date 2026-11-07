"""Typed HTTP client used by the portal API, agent worker, and MCP service.

The internal token is read from a mounted secret file. Error details from the
execution service are already safe to show; upstream transport errors are not.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from conductor.config import Settings
from conductor.errors import ConductorError

logger = logging.getLogger(__name__)


class ExecutionClientError(ConductorError):
    code = "execution_unavailable"
    status_code = 503
    public_message = "The execution service is temporarily unavailable."


class ExecutionClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self._client = httpx.AsyncClient(
            base_url=settings.execution_base_url.rstrip("/"),
            timeout=httpx.Timeout(120.0, connect=10.0),
            transport=transport,
            headers={"User-Agent": "cloudstack-conductor-api/0.1"},
        )
        self._token = settings.internal_token()

    async def __aenter__(self) -> ExecutionClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json,
                headers={"X-Internal-Token": self._token},
            )
        except httpx.HTTPError as exc:
            logger.warning("execution service request failed: %s", type(exc).__name__)
            raise ExecutionClientError() from exc
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            code = str(payload.get("code") or "execution_error")
            message = str(payload.get("message") or "The execution service rejected the request.")
            error = ConductorError(message)
            error.code = code
            error.status_code = response.status_code
            error.public_message = message
            raise error
        try:
            payload = response.json()
        except ValueError as exc:
            raise ExecutionClientError() from exc
        if not isinstance(payload, dict):
            raise ExecutionClientError()
        return payload

    # -- plans -------------------------------------------------------------
    async def create_plan(
        self,
        *,
        subject: str,
        action: str,
        parameters: dict[str, Any],
        idempotency_key: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/internal/v1/plans",
            json={
                "subject": subject,
                "action": action,
                "parameters": parameters,
                "idempotency_key": idempotency_key,
                "request_id": request_id,
            },
        )

    async def list_plans(self, subject: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/internal/v1/plans",
            params={"subject": subject, "limit": limit, "offset": offset},
        )

    async def get_plan(self, plan_id: str, subject: str) -> dict[str, Any]:
        return await self._request("GET", f"/internal/v1/plans/{plan_id}", params={"subject": subject})

    async def approve_plan(
        self,
        plan_id: str,
        *,
        subject: str,
        digest: str,
        note: str | None = None,
        client_fingerprint: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/internal/v1/plans/{plan_id}/approve",
            json={
                "subject": subject,
                "digest": digest,
                "note": note,
                "client_fingerprint": client_fingerprint,
                "request_id": request_id,
            },
        )

    async def reject_plan(self, plan_id: str, *, subject: str) -> dict[str, Any]:
        return await self._request("POST", f"/internal/v1/plans/{plan_id}/reject", json={"subject": subject})

    # -- quotes and resources ---------------------------------------------
    async def quote(self, *, subject: str, parameters: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/internal/v1/quotes", json={"subject": subject, "parameters": parameters})

    async def resources(self, *, subject: str, kind: str = "all", account_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"subject": subject, "kind": kind}
        if account_id:
            params["account_id"] = account_id
        return await self._request("GET", "/internal/v1/resources", params=params)

    async def credentials_status(self, subject: str) -> dict[str, Any]:
        return await self._request("GET", "/internal/v1/credentials/status", params={"subject": subject})

    async def cloud_health(self, subject: str) -> dict[str, Any]:
        return await self._request("GET", "/internal/v1/cloud/health", params={"subject": subject})

    # -- operations --------------------------------------------------------
    async def plan_operation(self, plan_id: str, subject: str) -> dict[str, Any]:
        return await self._request("GET", f"/internal/v1/plans/{plan_id}/operation", params={"subject": subject})

    async def usage_forecast(self, subject: str) -> dict[str, Any]:
        return await self._request("GET", "/internal/v1/usage/forecast", params={"subject": subject})

    async def list_operations(self, subject: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return await self._request(
            "GET",
            "/internal/v1/operations",
            params={"subject": subject, "limit": limit, "offset": offset},
        )

    async def get_operation(self, operation_id: str, subject: str) -> dict[str, Any]:
        return await self._request("GET", f"/internal/v1/operations/{operation_id}", params={"subject": subject})

    async def operation_events(self, operation_id: str, subject: str, *, after: int = 0) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/internal/v1/operations/{operation_id}/events",
            params={"subject": subject, "after": after},
        )

    async def cancel_operation(self, operation_id: str, subject: str) -> dict[str, Any]:
        return await self._request("POST", f"/internal/v1/operations/{operation_id}/cancel", json={"subject": subject})

    async def reconcile_operation(self, operation_id: str, subject: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"/internal/v1/operations/{operation_id}/reconcile", json={"subject": subject}
        )

    async def console_exchange(self, *, subject: str, lease_id: str, token: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/internal/v1/console/exchange",
            json={"subject": subject, "lease_id": lease_id, "token": token},
        )

    async def console_lease(self, *, subject: str, vm_id: str) -> dict[str, Any]:
        return await self._request("POST", "/internal/v1/console/lease", json={"subject": subject, "vm_id": vm_id})
