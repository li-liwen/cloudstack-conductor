"""Asynchronous, typed Apache CloudStack client.

The client receives explicit credentials. It never reads environment
variables, never caches a master key, and never logs request parameters or
secrets. Reads are retried with bounded backoff; mutations are not blindly
retried, because a lost response may still mean the cloud accepted the job.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from conductor.cloudstack.errors import (
    CloudStackAPIError,
    CloudStackJobFailed,
    CloudStackJobTimeout,
    CloudStackNotFound,
    CloudStackProtocolError,
)
from conductor.cloudstack.models import (
    Account,
    AsyncJob,
    Capabilities,
    ConsoleEndpoint,
    DiskOffering,
    Host,
    Network,
    RawUsageRecord,
    Router,
    ServiceOffering,
    Snapshot,
    Template,
    User,
    VirtualMachine,
    Volume,
    Zone,
)
from conductor.cloudstack.signing import sign
from conductor.errors import CloudStackUnavailable, ReconciliationRequired

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

_SAFE_READ_COMMANDS = frozenset(
    {
        "listzones",
        "listhosts",
        "listrouters",
        "listnetworks",
        "listnetworkofferings",
        "listserviceofferings",
        "listdiskofferings",
        "listtemplates",
        "listvirtualmachines",
        "listvolumes",
        "listsnapshots",
        "listaccounts",
        "listusers",
        "listcapabilities",
        "queryasyncjobresult",
        "listusagerecords",
        "getusagerecords",
    }
)


@dataclass(frozen=True)
class CloudStackCredentials:
    """Per-user credentials; constructed from the encrypted vault at call time."""

    api_url: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.api_url.startswith(("http://", "https://")):
            raise ValueError("CloudStack API URL must be HTTP(S)")
        if not self.api_key or not self.api_secret:
            raise ValueError("CloudStack credentials are incomplete")


@dataclass
class AsyncRateLimiter:
    """A tiny async token limiter shared by one client."""

    rate_per_second: float
    _next: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def acquire(self) -> None:
        if self.rate_per_second <= 0:
            return
        interval = 1.0 / self.rate_per_second
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + interval
        if wait:
            await asyncio.sleep(wait)


class CloudStackClient:
    """One client per (cloud, user). Close it after the request."""

    def __init__(
        self,
        credentials: CloudStackCredentials,
        *,
        timeout_seconds: float = 60.0,
        max_concurrent: int = 8,
        rate_per_second: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._credentials = credentials
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds))
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._limiter = AsyncRateLimiter(rate_per_second)
        self._sleep = sleep
        self.last_job_id: str | None = None
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            transport=transport,
            headers={
                "Accept": "application/json",
                "User-Agent": "cloudstack-conductor/0.1 (+cloudstack-adapter)",
            },
            follow_redirects=False,
        )

    async def __aenter__(self) -> CloudStackClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # -- low-level ---------------------------------------------------------
    def _params(self, command: str, params: dict[str, object]) -> dict[str, str]:
        merged: dict[str, object] = {
            "command": command,
            "response": "json",
            "apiKey": self._credentials.api_key,
            **{key: value for key, value in params.items() if value is not None and value != ""},
        }
        signature = sign(merged, self._credentials.api_secret)
        encoded = {key: str(value) for key, value in merged.items()}
        encoded["signature"] = signature
        return encoded

    @staticmethod
    def _error_from_body(body: dict[str, Any]) -> tuple[str | None, str | None]:
        for value in body.values():
            if isinstance(value, dict) and ("errorcode" in value or "errortext" in value):
                code = value.get("errorcode")
                text = value.get("errortext") or ""
                return (str(code) if code is not None else None), str(text)
        if "errorcode" in body:
            code = body.get("errorcode")
            text = body.get("errortext") or ""
            return (str(code) if code is not None else None), str(text)
        return None, None

    async def call(self, command: str, **params: object) -> dict[str, Any]:
        """Issue a read command, retrying only idempotent reads."""

        return await self._request(command, params, mutation=False)

    async def _request(self, command: str, params: dict[str, object], *, mutation: bool) -> dict[str, Any]:
        is_read = command.lower() in _SAFE_READ_COMMANDS
        attempts = 3 if is_read and not mutation else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with self._semaphore:
                    await self._limiter.acquire()
                    response = await self._client.get(self._credentials.api_url, params=self._params(command, params))
                break
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if mutation or attempt + 1 >= attempts:
                    if mutation:
                        # The cloud may have accepted the request before the
                        # response was lost. Never retry blindly.
                        raise ReconciliationRequired(
                            "the CloudStack response was lost while submitting a change"
                        ) from exc
                    raise CloudStackUnavailable("the CloudStack API could not be reached") from exc
                await self._sleep(0.2 * (2**attempt) + random.random() * 0.1)
        else:  # pragma: no cover - loop always breaks or raises
            raise CloudStackUnavailable("the CloudStack API could not be reached") from last_error

        if response.status_code >= 500:
            if mutation:
                raise ReconciliationRequired("the CloudStack API returned a server error while submitting a change")
            raise CloudStackUnavailable("the CloudStack API is temporarily unavailable")
        try:
            payload = response.json()
        except ValueError as exc:
            raise CloudStackProtocolError("the CloudStack API returned non-JSON content") from exc
        if not isinstance(payload, dict):
            raise CloudStackProtocolError("the CloudStack API returned an unexpected payload")
        code, text = self._error_from_body(payload)
        if code is not None:
            logger.warning("CloudStack command failed: command=%s code=%s", command, code)
            if code in {"401", "531", "530"}:
                raise CloudStackAPIError(code, detail=_safe(text))
            if code in {"404", "431"}:
                raise CloudStackNotFound(code, detail=_safe(text))
            raise CloudStackAPIError(code, detail=_safe(text))
        if response.status_code >= 400:
            raise CloudStackAPIError(response.status_code)
        return payload

    async def call_mutation_and_wait(
        self, command: str, *, timeout_seconds: float = 900.0, **params: object
    ) -> dict[str, Any]:
        payload = await self._request(command, params, mutation=True)
        body = _body(payload, command)
        job_id = body.get("jobid")
        if job_id:
            self.last_job_id = str(job_id)
            return await self.wait_for_job(str(job_id), timeout_seconds=timeout_seconds)
        self.last_job_id = None
        return body

    async def wait_for_job(self, job_id: str, *, timeout_seconds: float = 900.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        delay = 1.0
        while time.monotonic() < deadline:
            payload = await self.call("queryAsyncJobResult", jobid=job_id)
            body = _body(payload, "queryAsyncJobResult")
            try:
                job = AsyncJob.model_validate(body)
            except ValueError as exc:  # pragma: no cover - defensive
                raise CloudStackProtocolError("invalid job response") from exc
            if job.jobstatus == 1:
                result = job.jobresult or {}
                # Some commands return success with an embedded error object.
                code, text = self._error_from_body(result)
                if code is not None:
                    raise CloudStackJobFailed(job_id, code, detail=_safe(text))
                return result
            if job.jobstatus == 2:
                failure = job.jobresult or {}
                code, text = self._error_from_body(failure)
                raise CloudStackJobFailed(
                    job_id,
                    code or job.errorcode,
                    detail=_safe(text or job.errortext),
                )
            await self._sleep(delay + random.random() * 0.2)
            delay = min(delay * 1.7, 15.0)
        raise CloudStackJobTimeout(job_id)

    # -- pagination --------------------------------------------------------
    async def list_all(
        self,
        command: str,
        key: str,
        *,
        params: dict[str, object] | None = None,
        page_size: int = 100,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        page = 1
        while page <= max_pages:
            query = dict(params or {})
            query.update({"page": page, "pagesize": page_size})
            payload = await self.call(command, **query)
            body = _body(payload, command)
            items = body.get(key)
            if not isinstance(items, list) or not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                identity = str(item.get("id") or item.get("usageid") or "")
                if identity and identity in seen:
                    continue
                if identity:
                    seen.add(identity)
                results.append(item)
            count = body.get("count")
            try:
                total = int(count) if count is not None else None
            except (TypeError, ValueError):
                total = None
            if len(items) < page_size or (total is not None and len(results) >= total):
                break
            page += 1
        return results

    async def _typed_list(
        self,
        model: type[ModelT],
        command: str,
        key: str,
        *,
        params: dict[str, object] | None = None,
    ) -> list[ModelT]:
        raw = await self.list_all(command, key, params=params)
        parsed: list[ModelT] = []
        for item in raw:
            try:
                parsed.append(model.model_validate(item))
            except ValueError:
                logger.info("skipping an unparseable %s record", model.__name__)
        return parsed

    # -- reads -------------------------------------------------------------
    async def get_capabilities(self) -> Capabilities:
        payload = await self.call("listCapabilities")
        body = _body(payload, "listCapabilities")
        # CloudStack 4.22 nests the values under "capability"; older versions
        # return them at the top level. Accept both.
        if isinstance(body.get("capability"), dict):
            body = body["capability"]
        return Capabilities.model_validate(body)

    async def list_zones(self) -> list[Zone]:
        return await self._typed_list(Zone, "listZones", "zone")

    async def list_service_offerings(self, zone_id: str | None = None) -> list[ServiceOffering]:
        params = {"zoneid": zone_id} if zone_id else {}
        return await self._typed_list(ServiceOffering, "listServiceOfferings", "serviceoffering", params=params)

    async def list_disk_offerings(self, zone_id: str | None = None) -> list[DiskOffering]:
        params = {"zoneid": zone_id} if zone_id else {}
        return await self._typed_list(DiskOffering, "listDiskOfferings", "diskoffering", params=params)

    async def list_network_offerings(self) -> list[dict[str, Any]]:
        return await self.list_all("listNetworkOfferings", "networkoffering")

    async def list_templates(self, zone_id: str | None = None) -> list[Template]:
        params: dict[str, object] = {"templatefilter": "executable"}
        if zone_id:
            params["zoneid"] = zone_id
        return await self._typed_list(Template, "listTemplates", "template", params=params)

    async def list_networks(
        self,
        *,
        account_id: str | None = None,
        project_id: str | None = None,
        zone_id: str | None = None,
    ) -> list[Network]:
        params: dict[str, object] = {}
        if account_id:
            params["accountid"] = account_id
        if project_id:
            params["projectid"] = project_id
        if zone_id:
            params["zoneid"] = zone_id
        return await self._typed_list(Network, "listNetworks", "network", params=params)

    async def list_virtual_machines(
        self,
        *,
        account_id: str | None = None,
        project_id: str | None = None,
        zone_id: str | None = None,
    ) -> list[VirtualMachine]:
        params: dict[str, object] = {}
        if account_id:
            params["accountid"] = account_id
        if project_id:
            params["projectid"] = project_id
        if zone_id:
            params["zoneid"] = zone_id
        return await self._typed_list(VirtualMachine, "listVirtualMachines", "virtualmachine", params=params)

    async def get_virtual_machine(self, vm_id: str) -> VirtualMachine:
        payload = await self.call("listVirtualMachines", id=vm_id)
        items = _body(payload, "listVirtualMachines").get("virtualmachine") or []
        if not items:
            raise CloudStackNotFound("404")
        return VirtualMachine.model_validate(items[0])

    async def list_volumes(self, *, account_id: str | None = None, project_id: str | None = None) -> list[Volume]:
        params: dict[str, object] = {}
        if account_id:
            params["accountid"] = account_id
        if project_id:
            params["projectid"] = project_id
        return await self._typed_list(Volume, "listVolumes", "volume", params=params)

    async def list_snapshots(self, *, volume_id: str | None = None) -> list[Snapshot]:
        params = {"volumeid": volume_id} if volume_id else {}
        return await self._typed_list(Snapshot, "listSnapshots", "snapshot", params=params)

    async def list_accounts(self, *, domain_id: str | None = None) -> list[Account]:
        params = {"domainid": domain_id} if domain_id else {}
        return await self._typed_list(Account, "listAccounts", "account", params=params)

    async def list_users(self, *, account_id: str | None = None) -> list[User]:
        params = {"accountid": account_id} if account_id else {}
        return await self._typed_list(User, "listUsers", "user", params=params)

    async def list_hosts(self) -> list[Host]:
        return await self._typed_list(Host, "listHosts", "host")

    async def list_routers(self) -> list[Router]:
        return await self._typed_list(Router, "listRouters", "router")

    async def list_usage_records(
        self,
        start: datetime,
        end: datetime,
        *,
        account_id: str | None = None,
        domain_id: str | None = None,
        usage_type: int | None = None,
        page_size: int = 500,
    ) -> list[RawUsageRecord]:
        """Read usage records (requires an administrative reader credential).

        The collector uses a separate restricted reader role; customer
        operations never receive this credential.
        """

        params: dict[str, object] = {
            "startdate": _usage_date(start),
            "enddate": _usage_date(end),
        }
        if account_id:
            params["accountid"] = account_id
        if domain_id:
            params["domainid"] = domain_id
        if usage_type is not None:
            params["type"] = usage_type
        records: list[RawUsageRecord] = []
        page = 1
        while page <= 200:
            query = {**params, "page": page, "pagesize": page_size}
            payload = await self.call("listUsageRecords", **query)
            body = _body(payload, "listUsageRecords")
            items = body.get("usagerecord") or body.get("usage") or []
            if not items:
                break
            for item in items:
                try:
                    records.append(RawUsageRecord.model_validate(item))
                except ValueError:
                    logger.info("skipping an unparseable usage record")
            if len(items) < page_size:
                break
            page += 1
        return records

    # -- mutations ---------------------------------------------------------
    async def deploy_virtual_machine(
        self,
        *,
        service_offering_id: str,
        template_id: str,
        zone_id: str,
        network_ids: list[str],
        name: str,
        start: bool = True,
        disk_size_gib: object | None = None,
        disk_offering_id: str | None = None,
        keypair: str | None = None,
        group: str | None = None,
        account_id: str | None = None,
        project_id: str | None = None,
    ) -> VirtualMachine:
        params: dict[str, object] = {
            "serviceofferingid": service_offering_id,
            "templateid": template_id,
            "zoneid": zone_id,
            "networkids": ",".join(network_ids),
            "name": name,
            "startvm": "true" if start else "false",
        }
        if disk_size_gib is not None:
            params["rootdisksize"] = str(disk_size_gib)
        if disk_offering_id:
            params["diskofferingid"] = disk_offering_id
        if keypair:
            params["keypair"] = keypair
        if group:
            params["group"] = group
        if account_id:
            params["accountid"] = account_id
        if project_id:
            params["projectid"] = project_id
        body = await self.call_mutation_and_wait("deployVirtualMachine", **params)
        try:
            return VirtualMachine.model_validate(body)
        except ValueError:
            # Some versions return {"virtualmachine": {...}}.
            return VirtualMachine.model_validate(body.get("virtualmachine", body))

    async def start_virtual_machine(self, vm_id: str) -> VirtualMachine:
        body = await self.call_mutation_and_wait("startVirtualMachine", id=vm_id)
        return VirtualMachine.model_validate(body.get("virtualmachine", body))

    async def stop_virtual_machine(self, vm_id: str, *, force: bool = False) -> VirtualMachine:
        body = await self.call_mutation_and_wait("stopVirtualMachine", id=vm_id, forced="true" if force else "false")
        return VirtualMachine.model_validate(body.get("virtualmachine", body))

    async def reboot_virtual_machine(self, vm_id: str) -> VirtualMachine:
        body = await self.call_mutation_and_wait("rebootVirtualMachine", id=vm_id)
        return VirtualMachine.model_validate(body.get("virtualmachine", body))

    async def resize_virtual_machine(self, vm_id: str, offering_id: str) -> VirtualMachine:
        body = await self.call_mutation_and_wait(
            "changeServiceForVirtualMachine", id=vm_id, serviceofferingid=offering_id
        )
        return VirtualMachine.model_validate(body.get("virtualmachine", body))

    async def destroy_virtual_machine(self, vm_id: str, *, expunge: bool = False) -> dict[str, Any]:
        return await self.call_mutation_and_wait(
            "destroyVirtualMachine", id=vm_id, expunge="true" if expunge else "false"
        )

    async def restore_virtual_machine(self, vm_id: str, template_id: str | None = None) -> dict[str, Any]:
        params: dict[str, object] = {"virtualmachineid": vm_id}
        if template_id:
            params["templateid"] = template_id
        return await self.call_mutation_and_wait("restoreVirtualMachine", **params)

    async def reinstall_virtual_machine(self, vm_id: str, template_id: str | None = None) -> dict[str, Any]:
        params: dict[str, object] = {"virtualmachineid": vm_id}
        if template_id:
            params["templateid"] = template_id
        return await self.call_mutation_and_wait("reinstallVirtualMachine", **params)

    async def create_volume(
        self,
        *,
        name: str,
        zone_id: str,
        size_gib: object,
        disk_offering_id: str | None = None,
        snapshot_id: str | None = None,
        account_id: str | None = None,
        project_id: str | None = None,
    ) -> Volume:
        if not disk_offering_id and not snapshot_id:
            raise ValueError("create_volume requires an offering or a snapshot")
        params: dict[str, object] = {"name": name, "zoneid": zone_id}
        if disk_offering_id:
            params["diskofferingid"] = disk_offering_id
            params["size"] = str(size_gib)
        if snapshot_id:
            params["snapshotid"] = snapshot_id
        if account_id:
            params["accountid"] = account_id
        if project_id:
            params["projectid"] = project_id
        body = await self.call_mutation_and_wait("createVolume", **params)
        return Volume.model_validate(body.get("volume", body))

    async def attach_volume(self, volume_id: str, vm_id: str) -> Volume:
        body = await self.call_mutation_and_wait("attachVolume", id=volume_id, virtualmachineid=vm_id)
        return Volume.model_validate(body.get("volume", body))

    async def detach_volume(self, volume_id: str) -> Volume:
        body = await self.call_mutation_and_wait("detachVolume", id=volume_id)
        return Volume.model_validate(body.get("volume", body))

    async def resize_volume(self, volume_id: str, size_gib: object) -> Volume:
        body = await self.call_mutation_and_wait("resizeVolume", id=volume_id, size=str(size_gib))
        return Volume.model_validate(body.get("volume", body))

    async def delete_volume(self, volume_id: str) -> dict[str, Any]:
        return await self.call_mutation_and_wait("deleteVolume", id=volume_id)

    async def create_snapshot(self, volume_id: str, description: str | None = None) -> Snapshot:
        params: dict[str, object] = {"volumeid": volume_id}
        if description:
            params["name"] = description
        body = await self.call_mutation_and_wait("createSnapshot", **params)
        return Snapshot.model_validate(body.get("snapshot", body))

    async def create_network(
        self,
        *,
        name: str,
        display_text: str,
        zone_id: str,
        network_offering_id: str,
        cidr: str,
        gateway: str | None = None,
        account_id: str | None = None,
        project_id: str | None = None,
    ) -> Network:
        params: dict[str, object] = {
            "name": name,
            "displaytext": display_text,
            "zoneid": zone_id,
            "networkofferingid": network_offering_id,
            "gateway": gateway or str(ipaddress.ip_network(cidr)[1]),
            "netmask": str(ipaddress.ip_network(cidr).netmask),
        }
        if account_id:
            params["accountid"] = account_id
        if project_id:
            params["projectid"] = project_id
        body = await self.call_mutation_and_wait("createNetwork", **params)
        return Network.model_validate(body.get("network", body))

    async def delete_network(self, network_id: str) -> dict[str, Any]:
        return await self.call_mutation_and_wait("deleteNetwork", id=network_id)

    async def create_firewall_rule(
        self,
        *,
        network_id: str,
        protocol: str,
        start_port: int,
        end_port: int,
        cidrs: list[str],
        action: str = "allow",
        description: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, object] = {
            "networkid": network_id,
            "protocol": protocol,
            "startport": start_port,
            "endport": end_port,
            "cidrlist": ",".join(cidrs),
            "type": action,
        }
        if description:
            params["description"] = description
        return await self.call_mutation_and_wait("createFirewallRule", **params)

    async def delete_firewall_rule(self, rule_id: str) -> dict[str, Any]:
        return await self.call_mutation_and_wait("deleteFirewallRule", id=rule_id)

    async def get_user_keys(self, user_id: str) -> tuple[str, str] | None:
        payload = await self.call("getUserKeys", userid=user_id)
        body = _body(payload, "getUserKeys")
        keys = body.get("userkeys", body)
        if not isinstance(keys, dict):
            return None
        api_key = keys.get("apikey")
        secret_key = keys.get("secretkey")
        if api_key and secret_key:
            return str(api_key), str(secret_key)
        return None

    async def register_user_keys(self, user_id: str) -> tuple[str, str]:
        body = await self.call_mutation_and_wait("registerUserKeys", id=user_id)
        keys = body.get("userkeys", body)
        if not isinstance(keys, dict) or not keys.get("apikey") or not keys.get("secretkey"):
            raise CloudStackProtocolError("the user key response was incomplete")
        return str(keys["apikey"]), str(keys["secretkey"])

    async def register_ssh_key(self, name: str, public_key: str) -> dict[str, Any]:
        payload = await self._request("registerSSHKeyPair", {"name": name, "publickey": public_key}, mutation=True)
        return _body(payload, "registerSSHKeyPair")

    async def create_console_endpoint(self, vm_id: str) -> ConsoleEndpoint:
        payload = await self._request("createConsoleEndpoint", {"virtualmachineid": vm_id}, mutation=False)
        body = _body(payload, "createConsoleEndpoint")
        candidate: object = body
        for _ in range(3):
            if isinstance(candidate, dict):
                for key in ("url", "consoleurl", "consoleendpoint"):
                    if key in candidate:
                        candidate = candidate[key]
                        break
                else:
                    break
            elif isinstance(candidate, str):
                break
            else:
                break
        if isinstance(candidate, dict):
            candidate = candidate.get("url")
        if not isinstance(candidate, str):
            raise CloudStackProtocolError("the console endpoint response had no URL")
        parsed = urllib.parse.urlparse(candidate)
        try:
            port = parsed.port or (443 if parsed.scheme in {"https", "wss"} else 80)
        except ValueError as exc:
            raise CloudStackProtocolError("the console endpoint port was invalid") from exc
        return ConsoleEndpoint(url=candidate, host=parsed.hostname, port=port)


def _safe(text: str) -> str:
    """Reduce upstream text to something safe for private logs only."""

    return text.replace("\n", " ")[:300]


def _body(payload: dict[str, Any], command: str) -> dict[str, Any]:
    key = command.lower() + "response"
    body = payload.get(key)
    if isinstance(body, dict):
        return body
    # Some error and legacy responses are nested differently; use the first
    # dict value that is not a list.
    for value in payload.values():
        if isinstance(value, dict):
            return value
    return payload


def _usage_date(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d")


def periodic_window(now: datetime, days: int = 1) -> tuple[datetime, datetime]:
    end = now.astimezone(UTC)
    return end - timedelta(days=days), end
