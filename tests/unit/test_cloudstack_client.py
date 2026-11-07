from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from conductor.cloudstack.client import CloudStackClient, CloudStackCredentials
from conductor.cloudstack.errors import CloudStackAPIError, CloudStackJobFailed
from conductor.cloudstack.signing import sign
from conductor.errors import ReconciliationRequired

API = "http://cloudstack.example.com/client/api"


def _client() -> CloudStackClient:
    return CloudStackClient(
        CloudStackCredentials(api_url=API, api_key="demo-key", api_secret="demo-secret"),
        rate_per_second=0,
        sleep=_no_sleep,
    )


async def _no_sleep(_: float) -> None:
    return None


def _assert_signed(request: httpx.Request) -> None:
    params = {key: value[0] for key, value in parse_qs(urlparse(str(request.url)).query).items()}
    signature = params.pop("signature")
    assert params["apiKey"] == "demo-key"
    assert sign(params, "demo-secret") == signature


@respx.mock
async def test_lists_are_parsed_and_signed() -> None:
    route = respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json={
                "listvirtualmachinesresponse": {
                    "count": 1,
                    "virtualmachine": [
                        {
                            "id": "11111111-1111-4111-8111-111111111111",
                            "name": "demo-web",
                            "state": "Running",
                            "serviceofferingid": "22222222-2222-4222-8222-222222222222",
                            "nic": [{"ipaddress": "192.0.2.10"}],
                        }
                    ],
                }
            },
        )
    )
    async with _client() as client:
        machines = await client.list_virtual_machines()
    assert machines[0].name == "demo-web"
    assert machines[0].ip_addresses == ["192.0.2.10"]
    _assert_signed(route.calls[0].request)


@respx.mock
async def test_pagination_deduplicates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(parse_qs(urlparse(str(request.url)).query)["page"][0])
        items = {
            1: [{"id": "a" * 8 + "-1111-4111-8111-111111111111", "name": "one"}],
            2: [{"id": "b" * 8 + "-2222-4222-8222-222222222222", "name": "two"}],
        }.get(page, [])
        return httpx.Response(200, json={"listvirtualmachinesresponse": {"count": 2, "virtualmachine": items}})

    respx.get(API).mock(side_effect=handler)
    async with _client() as client:
        rows = await client.list_all("listVirtualMachines", "virtualmachine", page_size=1)
    assert [row["name"] for row in rows] == ["one", "two"]


@respx.mock
async def test_api_error_is_safe() -> None:
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json={"errorresponse": {"errorcode": 401, "errortext": "unable to verify"}},
        )
    )
    async with _client() as client:
        with pytest.raises(CloudStackAPIError) as exc:
            await client.list_zones()
    assert "unable to verify" not in exc.value.public_message


@respx.mock
async def test_mutation_timeout_requires_reconciliation() -> None:
    respx.get(API).mock(side_effect=httpx.ConnectTimeout("lost"))
    async with _client() as client:
        with pytest.raises(ReconciliationRequired):
            await client.call_mutation_and_wait("startVirtualMachine", id="vm")


@respx.mock
async def test_async_job_success_and_failure() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(urlparse(str(request.url)).query)
        command = params["command"][0].lower()
        if command == "startvirtualmachine":
            return httpx.Response(200, json={"startvirtualmachineresponse": {"jobid": "job-1"}})
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"queryasyncjobresultresponse": {"jobstatus": 0}})
        return httpx.Response(
            200,
            json={
                "queryasyncjobresultresponse": {
                    "jobstatus": 1,
                    "jobresult": {"virtualmachine": {"id": "vm-1", "name": "demo", "state": "Running"}},
                }
            },
        )

    respx.get(API).mock(side_effect=handler)
    async with _client() as client:
        machine = await client.start_virtual_machine("vm-1")
    assert machine.state == "Running"
    assert calls["n"] == 2


@respx.mock
async def test_job_failure_is_reported() -> None:
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json={
                "queryasyncjobresultresponse": {
                    "jobstatus": 2,
                    "jobresult": {"errorcode": 530, "errortext": "failed"},
                }
            },
        )
    )
    async with _client() as client:
        with pytest.raises(CloudStackJobFailed):
            await client.wait_for_job("job-1")


@respx.mock
async def test_console_endpoint_parsing() -> None:
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json={
                "createconsoleendpointresponse": {
                    "consoleendpoint": {"url": "wss://console.example.com/websockify?token=abc"}
                }
            },
        )
    )
    async with _client() as client:
        endpoint = await client.create_console_endpoint("vm-1")
    assert endpoint.host == "console.example.com"
    assert endpoint.port == 443


@respx.mock
async def test_usage_records_parse_dates_and_quantities() -> None:
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json={
                "listusagerecordsresponse": {
                    "count": 1,
                    "usagerecord": [
                        {
                            "usageid": "usage-1",
                            "accountid": "account",
                            "resourceid": "resource",
                            "resourcetype": "VirtualMachine",
                            "usagetype": 1,
                            "usage": "24",
                            "startdate": "2026-11-01T00:00:00+0000",
                            "enddate": "2026-11-01T23:59:59+0000",
                        }
                    ],
                }
            },
        )
    )
    async with _client() as client:
        records = await client.list_usage_records(datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 11, 2, tzinfo=UTC))
    assert records[0].usageid == "usage-1"
    assert records[0].startdate == datetime(2026, 11, 1, tzinfo=UTC)
