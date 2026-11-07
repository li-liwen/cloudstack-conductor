"""Synthetic CloudStack API for the public demo.

This is deliberately fictional: tenants, offerings, rates, identifiers, and
hostnames are invented. It verifies API signatures so the real adapter is
exercised rather than bypassed.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from demo.fixtures import (
    ACCOUNT_ID,
    ACCOUNT_NAME,
    ALMA_TEMPLATE_ID,
    DB_INSTANCE_ID,
    DISK_OFFERING_ID,
    DOMAIN_ID,
    DOMAIN_NAME,
    GPU_OFFERING_ID,
    MEDIUM_OFFERING_ID,
    NETWORK_ID,
    NETWORK_OFFERING_ID,
    SHARED_NETWORK_ID,
    SMALL_OFFERING_ID,
    UBUNTU_TEMPLATE_ID,
    USER_ID,
    WEB_INSTANCE_ID,
    WEB_VOLUME_ID,
    ZONE_ID,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from conductor.cloudstack.signing import sign

API_KEY = os.environ.get("DEMO_API_KEY", "demo-api-key")
API_SECRET = os.environ.get("DEMO_API_SECRET", "demo-api-secret")
VERIFY_SIGNATURE = os.environ.get("DEMO_VERIFY_SIGNATURE", "true").lower() == "true"


def _now() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%d'T'%H:%M:%S%z")[:-2] + ":" + datetime.now(tz=UTC).strftime("%z")[-2:]


def _iso(days_ago: int = 0) -> str:
    moment = datetime.now(tz=UTC) - timedelta(days=days_ago)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "+0000")


class DemoState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.instances: dict[str, dict[str, Any]] = {}
        self.volumes: dict[str, dict[str, Any]] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}
        self.networks: dict[str, dict[str, Any]] = {}
        self.firewall_rules: dict[str, dict[str, Any]] = {}
        self.ssh_keys: dict[str, str] = {}
        self.seed()

    def seed(self) -> None:
        self.instances = {
            WEB_INSTANCE_ID: self._vm(WEB_INSTANCE_ID, "demo-web", "Running", SMALL_OFFERING_ID, UBUNTU_TEMPLATE_ID),
            DB_INSTANCE_ID: self._vm(DB_INSTANCE_ID, "demo-db", "Stopped", MEDIUM_OFFERING_ID, ALMA_TEMPLATE_ID),
        }
        self.volumes = {
            WEB_VOLUME_ID: self._volume(WEB_VOLUME_ID, "demo-web-root", 42949672960, WEB_INSTANCE_ID, "ROOT"),
            "13131313-1313-4313-8313-131313131313": self._volume(
                "13131313-1313-4313-8313-131313131313",
                "demo-web-data",
                21474836480,
                WEB_INSTANCE_ID,
                "DATADISK",
            ),
            "14141414-1414-4414-8414-141414141414": self._volume(
                "14141414-1414-4414-8414-141414141414",
                "demo-db-root",
                85899345920,
                DB_INSTANCE_ID,
                "ROOT",
            ),
        }
        self.networks = {
            NETWORK_ID: {
                "id": NETWORK_ID,
                "name": "demo-isolated",
                "displaytext": "Demo isolated network",
                "type": "Isolated",
                "state": "Allocated",
                "cidr": "192.0.2.0/24",
                "gateway": "192.0.2.1",
                "netmask": "255.255.255.0",
                "zoneid": ZONE_ID,
                "zonename": "demo-zone",
                "account": ACCOUNT_NAME,
                "accountid": ACCOUNT_ID,
                "domainid": DOMAIN_ID,
                "networkofferingid": NETWORK_OFFERING_ID,
                "networkofferingname": "Demo Isolated Network",
                "isdefault": True,
                "traffictype": "Guest",
            },
            SHARED_NETWORK_ID: {
                "id": SHARED_NETWORK_ID,
                "name": "demo-shared",
                "displaytext": "Demo shared network",
                "type": "Shared",
                "state": "Setup",
                "cidr": "198.51.100.0/24",
                "gateway": "198.51.100.1",
                "netmask": "255.255.255.0",
                "zoneid": ZONE_ID,
                "zonename": "demo-zone",
                "account": ACCOUNT_NAME,
                "accountid": ACCOUNT_ID,
                "domainid": DOMAIN_ID,
                "networkofferingid": NETWORK_OFFERING_ID,
                "networkofferingname": "Demo Shared Network",
                "isdefault": False,
                "traffictype": "Guest",
            },
        }

    def _vm(self, identifier: str, name: str, state: str, offering: str, template: str) -> dict[str, Any]:
        offering_name = {
            SMALL_OFFERING_ID: "demo.small",
            MEDIUM_OFFERING_ID: "demo.medium",
            GPU_OFFERING_ID: "demo.gpu",
        }[offering]
        template_name = {
            UBUNTU_TEMPLATE_ID: "ubuntu-24.04-demo",
            ALMA_TEMPLATE_ID: "almalinux-9-demo",
        }[template]
        return {
            "id": identifier,
            "name": name,
            "displayname": name,
            "state": state,
            "zoneid": ZONE_ID,
            "zonename": "demo-zone",
            "templateid": template,
            "templatename": template_name,
            "serviceofferingid": offering,
            "serviceofferingname": offering_name,
            "account": ACCOUNT_NAME,
            "accountid": ACCOUNT_ID,
            "domain": DOMAIN_NAME,
            "domainid": DOMAIN_ID,
            "hostid": "15151515-1515-4515-8515-151515151515",
            "hostname": "demo-host",
            "hypervisor": "KVM",
            "created": _iso(1),
            "nic": [
                {
                    "networkid": NETWORK_ID,
                    "ipaddress": "192.0.2.10" if identifier == WEB_INSTANCE_ID else "192.0.2.11",
                    "gateway": "192.0.2.1",
                    "netmask": "255.255.255.0",
                }
            ],
            "ipaddress": "192.0.2.10" if identifier == WEB_INSTANCE_ID else "192.0.2.11",
            "passwordenabled": False,
            "tags": [],
        }

    def _volume(self, identifier: str, name: str, size: int, vm_id: str | None, volume_type: str) -> dict[str, Any]:
        return {
            "id": identifier,
            "name": name,
            "type": volume_type,
            "size": size,
            "state": "Ready",
            "virtualmachineid": vm_id,
            "vmname": self.instances.get(vm_id, {}).get("name") if vm_id else None,
            "diskofferingid": DISK_OFFERING_ID,
            "diskofferingname": "demo-storage",
            "zoneid": ZONE_ID,
            "zonename": "demo-zone",
            "account": ACCOUNT_NAME,
            "accountid": ACCOUNT_ID,
            "domainid": DOMAIN_ID,
            "created": _iso(1),
            "storage": "shared",
        }

    def job(self, result: dict[str, Any] | None = None) -> str:
        job_id = str(uuid.uuid4())
        self.jobs[job_id] = {"jobstatus": 1, "jobresult": result or {}}
        return job_id


STATE = DemoState()
app = FastAPI(title="CloudStack Conductor demo CloudStack", docs_url=None, openapi_url=None)


def _error(code: int, text: str) -> JSONResponse:
    return JSONResponse(status_code=200, content={"errorresponse": {"errorcode": code, "errortext": text}})


def _response(command: str, body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(content={command.lower() + "response": body})


def _list(key: str, items: list[dict[str, Any]], params: dict[str, str]) -> JSONResponse:
    page = int(params.get("page", "1"))
    size = int(params.get("pagesize", "100"))
    start = max(0, (page - 1) * size)
    page_items = items[start : start + size]
    return _response(
        params["command"],
        {"count": len(items), key: page_items} if page_items else {"count": len(items)},
    )


@app.get("/client/api")
async def cloudstack_api(request: Request) -> JSONResponse:
    params = dict(request.query_params.items())
    if VERIFY_SIGNATURE:
        provided = params.pop("signature", None)
        if not provided or params.get("apiKey") != API_KEY:
            return _error(401, "unable to verify user credentials")
        if sign(params, API_SECRET) != provided:
            return _error(401, "unable to verify user credentials")
    command = params.get("command", "")
    handler = COMMANDS.get(command.lower())
    if handler is None:
        return _error(530, "unsupported command in the demo cloud")
    async with STATE.lock:
        return handler(params)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "demo-cloudstack"}


def _zone() -> JSONResponse:
    return _response(
        "listZones",
        {
            "count": 1,
            "zone": [
                {
                    "id": ZONE_ID,
                    "name": "demo-zone",
                    "type": "Advanced",
                    "networktype": "Advanced",
                    "state": "Enabled",
                    "allocationstate": "Enabled",
                }
            ],
        },
    )


def _capabilities() -> JSONResponse:
    return _response(
        "listCapabilities",
        {
            "cloudstackversion": "4.22.1.1-demo",
            "securitygroupsenabled": False,
            "dynamicrolesenabled": True,
            "kvmsnapshotenabled": False,
            "userpublictemplateenabled": False,
        },
    )


def _offerings(params: dict[str, str]) -> JSONResponse:
    offerings = [
        {
            "id": SMALL_OFFERING_ID,
            "name": "demo.small",
            "displaytext": "2 vCPU / 4 GiB",
            "cpunumber": 2,
            "cpuspeed": 2000,
            "memory": 4096,
            "gpunumber": 0,
            "offerha": False,
            "iscustomized": False,
        },
        {
            "id": MEDIUM_OFFERING_ID,
            "name": "demo.medium",
            "displaytext": "4 vCPU / 8 GiB",
            "cpunumber": 4,
            "cpuspeed": 2000,
            "memory": 8192,
            "gpunumber": 0,
            "offerha": False,
            "iscustomized": False,
        },
        {
            "id": GPU_OFFERING_ID,
            "name": "demo.gpu",
            "displaytext": "8 vCPU / 32 GiB / 1 GPU",
            "cpunumber": 8,
            "cpuspeed": 2400,
            "memory": 32768,
            "gpunumber": 1,
            "offerha": True,
            "iscustomized": False,
        },
    ]
    return _list("serviceoffering", offerings, params | {"command": "listServiceOfferings"})


def _disk_offerings(params: dict[str, str]) -> JSONResponse:
    return _list(
        "diskoffering",
        [
            {
                "id": DISK_OFFERING_ID,
                "name": "demo-storage",
                "displaytext": "Demo storage",
                "disksize": 0,
                "iscustomized": True,
                "storagetype": "shared",
            }
        ],
        params | {"command": "listDiskOfferings"},
    )


def _templates(params: dict[str, str]) -> JSONResponse:
    return _list(
        "template",
        [
            {
                "id": UBUNTU_TEMPLATE_ID,
                "name": "ubuntu-24.04-demo",
                "displaytext": "Ubuntu 24.04 demo template",
                "ostypeid": "16161616-1616-4616-8616-161616161616",
                "ostypename": "Ubuntu 24.04",
                "size": 3221225472,
                "format": "QCOW2",
                "hypervisor": "KVM",
                "isready": True,
                "ispublic": True,
                "zoneid": ZONE_ID,
                "templatetype": "USER",
            },
            {
                "id": ALMA_TEMPLATE_ID,
                "name": "almalinux-9-demo",
                "displaytext": "AlmaLinux 9 demo template",
                "ostypeid": "17171717-1717-4717-8717-171717171717",
                "ostypename": "AlmaLinux 9",
                "size": 3221225472,
                "format": "QCOW2",
                "hypervisor": "KVM",
                "isready": True,
                "ispublic": True,
                "zoneid": ZONE_ID,
                "templatetype": "USER",
            },
        ],
        params | {"command": "listTemplates"},
    )


def _network_offerings(params: dict[str, str]) -> JSONResponse:
    return _list(
        "networkoffering",
        [
            {
                "id": NETWORK_OFFERING_ID,
                "name": "Demo Isolated Network",
                "displaytext": "Demo isolated network offering",
                "guestiptype": "Isolated",
                "traffictype": "Guest",
                "state": "Enabled",
            }
        ],
        params | {"command": "listNetworkOfferings"},
    )


def _networks(params: dict[str, str]) -> JSONResponse:
    items = list(STATE.networks.values())
    return _list("network", items, params | {"command": "listNetworks"})


def _vms(params: dict[str, str]) -> JSONResponse:
    items = list(STATE.instances.values())
    if params.get("id"):
        items = [item for item in items if item["id"] == params["id"]]
    return _list("virtualmachine", items, params | {"command": "listVirtualMachines"})


def _volumes(params: dict[str, str]) -> JSONResponse:
    items = list(STATE.volumes.values())
    if params.get("id"):
        items = [item for item in items if item["id"] == params["id"]]
    return _list("volume", items, params | {"command": "listVolumes"})


def _snapshots(params: dict[str, str]) -> JSONResponse:
    items = list(STATE.snapshots.values())
    if params.get("volumeid"):
        items = [item for item in items if item.get("volumeid") == params["volumeid"]]
    return _list("snapshot", items, params | {"command": "listSnapshots"})


def _accounts(params: dict[str, str]) -> JSONResponse:
    return _list(
        "account",
        [
            {
                "id": ACCOUNT_ID,
                "name": ACCOUNT_NAME,
                "accounttype": 0,
                "domain": DOMAIN_NAME,
                "domainid": DOMAIN_ID,
                "state": "Enabled",
            }
        ],
        params | {"command": "listAccounts"},
    )


def _users(params: dict[str, str]) -> JSONResponse:
    return _list(
        "user",
        [
            {
                "id": USER_ID,
                "username": "demo-user",
                "account": ACCOUNT_NAME,
                "accountid": ACCOUNT_ID,
                "domain": DOMAIN_NAME,
                "domainid": DOMAIN_ID,
                "state": "Enabled",
            }
        ],
        params | {"command": "listUsers"},
    )


def _user_keys(params: dict[str, str]) -> JSONResponse:
    return _response("getUserKeys", {"userkeys": {"userid": USER_ID, "apikey": API_KEY, "secretkey": API_SECRET}})


def _register_user_keys(params: dict[str, str]) -> JSONResponse:
    job_id = STATE.job({"userkeys": {"userid": USER_ID, "apikey": API_KEY, "secretkey": API_SECRET}})
    return _response("registerUserKeys", {"jobid": job_id})


def _deploy(params: dict[str, str]) -> JSONResponse:
    name = params.get("name", "demo-instance")
    if any(vm["name"] == name for vm in STATE.instances.values()):
        return _error(431, "a virtual machine with that name already exists")
    offering = params.get("serviceofferingid", SMALL_OFFERING_ID)
    template = params.get("templateid", UBUNTU_TEMPLATE_ID)
    if offering not in {SMALL_OFFERING_ID, MEDIUM_OFFERING_ID, GPU_OFFERING_ID}:
        return _error(431, "unknown service offering")
    if template not in {UBUNTU_TEMPLATE_ID, ALMA_TEMPLATE_ID}:
        return _error(431, "unknown template")
    network_ids = (params.get("networkids") or NETWORK_ID).split(",")
    if any(network_id not in STATE.networks for network_id in network_ids):
        return _error(431, "unknown network")
    identifier = str(uuid.uuid4())
    vm = STATE._vm(
        identifier,
        name,
        "Running" if params.get("startvm", "true") == "true" else "Stopped",
        offering,
        template,
    )
    STATE.instances[identifier] = vm
    volume = STATE._volume(str(uuid.uuid4()), f"{name}-root", 42949672960, identifier, "ROOT")
    STATE.volumes[volume["id"]] = volume
    return _response("deployVirtualMachine", {"jobid": STATE.job({"virtualmachine": vm})})


def _start(params: dict[str, str]) -> JSONResponse:
    vm = STATE.instances.get(params.get("id", ""))
    if vm is None:
        return _error(431, "virtual machine not found")
    vm["state"] = "Running"
    return _response("startVirtualMachine", {"jobid": STATE.job({"virtualmachine": vm})})


def _stop(params: dict[str, str]) -> JSONResponse:
    vm = STATE.instances.get(params.get("id", ""))
    if vm is None:
        return _error(431, "virtual machine not found")
    vm["state"] = "Stopped"
    return _response("stopVirtualMachine", {"jobid": STATE.job({"virtualmachine": vm})})


def _reboot(params: dict[str, str]) -> JSONResponse:
    vm = STATE.instances.get(params.get("id", ""))
    if vm is None:
        return _error(431, "virtual machine not found")
    return _response("rebootVirtualMachine", {"jobid": STATE.job({"virtualmachine": vm})})


def _resize_vm(params: dict[str, str]) -> JSONResponse:
    vm = STATE.instances.get(params.get("id", ""))
    offering = params.get("serviceofferingid")
    if vm is None:
        return _error(431, "virtual machine not found")
    names = {
        SMALL_OFFERING_ID: "demo.small",
        MEDIUM_OFFERING_ID: "demo.medium",
        GPU_OFFERING_ID: "demo.gpu",
    }
    if offering not in names:
        return _error(431, "unknown service offering")
    vm["serviceofferingid"] = offering
    vm["serviceofferingname"] = names[offering]
    return _response("changeServiceForVirtualMachine", {"jobid": STATE.job({"virtualmachine": vm})})


def _destroy(params: dict[str, str]) -> JSONResponse:
    vm = STATE.instances.pop(params.get("id", ""), None)
    if vm is None:
        return _error(431, "virtual machine not found")
    for volume in list(STATE.volumes.values()):
        if volume.get("virtualmachineid") == vm["id"]:
            volume["virtualmachineid"] = None
            volume["vmname"] = None
    return _response("destroyVirtualMachine", {"jobid": STATE.job({"virtualmachine": vm})})


def _restore(params: dict[str, str]) -> JSONResponse:
    vm = STATE.instances.get(params.get("virtualmachineid", ""))
    if vm is None:
        return _error(431, "virtual machine not found")
    return _response("restoreVirtualMachine", {"jobid": STATE.job({"virtualmachine": vm})})


def _reinstall(params: dict[str, str]) -> JSONResponse:
    vm = STATE.instances.get(params.get("virtualmachineid", ""))
    if vm is None:
        return _error(431, "virtual machine not found")
    return _response("reinstallVirtualMachine", {"jobid": STATE.job({"virtualmachine": vm})})


def _create_volume(params: dict[str, str]) -> JSONResponse:
    size_gib = int(params.get("size", "20"))
    volume = STATE._volume(str(uuid.uuid4()), params.get("name", "demo-volume"), size_gib * 1024**3, None, "DATADISK")
    STATE.volumes[volume["id"]] = volume
    return _response("createVolume", {"jobid": STATE.job({"volume": volume})})


def _attach_volume(params: dict[str, str]) -> JSONResponse:
    volume = STATE.volumes.get(params.get("id", ""))
    vm = STATE.instances.get(params.get("virtualmachineid", ""))
    if volume is None or vm is None:
        return _error(431, "volume or virtual machine not found")
    volume["virtualmachineid"] = vm["id"]
    volume["vmname"] = vm["name"]
    return _response("attachVolume", {"jobid": STATE.job({"volume": volume})})


def _detach_volume(params: dict[str, str]) -> JSONResponse:
    volume = STATE.volumes.get(params.get("id", ""))
    if volume is None:
        return _error(431, "volume not found")
    volume["virtualmachineid"] = None
    volume["vmname"] = None
    return _response("detachVolume", {"jobid": STATE.job({"volume": volume})})


def _resize_volume(params: dict[str, str]) -> JSONResponse:
    volume = STATE.volumes.get(params.get("id", ""))
    if volume is None:
        return _error(431, "volume not found")
    volume["size"] = int(params.get("size", "20")) * 1024**3
    return _response("resizeVolume", {"jobid": STATE.job({"volume": volume})})


def _delete_volume(params: dict[str, str]) -> JSONResponse:
    volume = STATE.volumes.pop(params.get("id", ""), None)
    if volume is None:
        return _error(431, "volume not found")
    return _response("deleteVolume", {"jobid": STATE.job({})})


def _create_snapshot(params: dict[str, str]) -> JSONResponse:
    volume = STATE.volumes.get(params.get("volumeid", ""))
    if volume is None:
        return _error(431, "volume not found")
    snapshot = {
        "id": str(uuid.uuid4()),
        "name": params.get("name") or f"snapshot-{volume['name']}",
        "volumeid": volume["id"],
        "volumename": volume["name"],
        "state": "BackedUp",
        "snapshottype": "MANUAL",
        "created": _iso(),
        "accountid": ACCOUNT_ID,
        "domainid": DOMAIN_ID,
        "zoneid": ZONE_ID,
    }
    STATE.snapshots[snapshot["id"]] = snapshot
    return _response("createSnapshot", {"jobid": STATE.job({"snapshot": snapshot})})


def _create_network(params: dict[str, str]) -> JSONResponse:
    identifier = str(uuid.uuid4())
    network = {
        "id": identifier,
        "name": params.get("name", "demo-network"),
        "displaytext": params.get("displaytext", "demo network"),
        "type": "Isolated",
        "state": "Allocated",
        "cidr": params.get("cidr", "198.51.100.0/24"),
        "gateway": params.get("gateway", "198.51.100.1"),
        "netmask": params.get("netmask", "255.255.255.0"),
        "zoneid": ZONE_ID,
        "account": ACCOUNT_NAME,
        "accountid": ACCOUNT_ID,
        "domainid": DOMAIN_ID,
        "networkofferingid": params.get("networkofferingid", NETWORK_OFFERING_ID),
        "isdefault": False,
    }
    STATE.networks[identifier] = network
    return _response("createNetwork", {"jobid": STATE.job({"network": network})})


def _delete_network(params: dict[str, str]) -> JSONResponse:
    if STATE.networks.pop(params.get("id", ""), None) is None:
        return _error(431, "network not found")
    return _response("deleteNetwork", {"jobid": STATE.job({})})


def _create_firewall(params: dict[str, str]) -> JSONResponse:
    rule = {
        "id": str(uuid.uuid4()),
        "networkid": params.get("networkid"),
        "protocol": params.get("protocol", "tcp"),
        "startport": int(params.get("startport", 0)),
        "endport": int(params.get("endport", 0)),
        "cidrlist": params.get("cidrlist", ""),
        "state": "Active",
    }
    STATE.firewall_rules[rule["id"]] = rule
    return _response("createFirewallRule", {"jobid": STATE.job({"firewallrule": rule})})


def _delete_firewall(params: dict[str, str]) -> JSONResponse:
    if STATE.firewall_rules.pop(params.get("id", ""), None) is None:
        return _error(431, "firewall rule not found")
    return _response("deleteFirewallRule", {"jobid": STATE.job({})})


def _register_key(params: dict[str, str]) -> JSONResponse:
    STATE.ssh_keys[params.get("name", "demo-key")] = params.get("publickey", "")
    return _response(
        "registerSSHKeyPair",
        {
            "keypair": {
                "name": params.get("name", "demo-key"),
                "fingerprint": hashlib.sha256(params.get("publickey", "").encode()).hexdigest()[:32],
            }
        },
    )


def _console(params: dict[str, str]) -> JSONResponse:
    return _response(
        "createConsoleEndpoint",
        {
            "consoleendpoint": {
                "url": "wss://console.example.com/websockify?token=demo-synthetic-token",
                "host": "console.example.com",
            }
        },
    )


def _job_result(params: dict[str, str]) -> JSONResponse:
    job = STATE.jobs.get(params.get("jobid", ""))
    if job is None:
        return _error(431, "job not found")
    return _response("queryAsyncJobResult", job)


def _usage(params: dict[str, str]) -> JSONResponse:
    records = []
    now = datetime.now(tz=UTC)
    for days in range(0, 10):
        day = now - timedelta(days=days)
        day_key = day.strftime("%Y%m%d")
        records.append(
            {
                "usageid": f"usage-web-{day_key}",
                "account": ACCOUNT_NAME,
                "accountid": ACCOUNT_ID,
                "domainid": DOMAIN_ID,
                "zoneid": ZONE_ID,
                "resourcetype": "VirtualMachine",
                "resourceid": WEB_INSTANCE_ID,
                "resourcename": "demo-web",
                "virtualmachineid": WEB_INSTANCE_ID,
                "offeringid": SMALL_OFFERING_ID,
                "usagetype": 1,
                "description": "Running VMs",
                "usage": "24",
                "rawusage": "24",
                "startdate": day.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                "enddate": day.replace(hour=23, minute=59, second=59, microsecond=0).isoformat(),
            }
        )
        records.append(
            {
                "usageid": f"usage-volume-{day_key}",
                "account": ACCOUNT_NAME,
                "accountid": ACCOUNT_ID,
                "domainid": DOMAIN_ID,
                "zoneid": ZONE_ID,
                "resourcetype": "Volume",
                "resourceid": WEB_VOLUME_ID,
                "resourcename": "demo-web-root",
                "usagetype": 5,
                "description": "Volume usage",
                "usage": str(40 * 24),
                "rawusage": str(40 * 24),
                "startdate": day.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                "enddate": day.replace(hour=23, minute=59, second=59, microsecond=0).isoformat(),
            }
        )
    return _list("usagerecord", records, params | {"command": "listUsageRecords"})


COMMANDS = {
    "listzones": lambda params: _zone(),
    "listcapabilities": lambda params: _capabilities(),
    "listserviceofferings": _offerings,
    "listdiskofferings": _disk_offerings,
    "listtemplates": _templates,
    "listnetworkofferings": _network_offerings,
    "listnetworks": _networks,
    "listvirtualmachines": _vms,
    "listvolumes": _volumes,
    "listsnapshots": _snapshots,
    "listaccounts": _accounts,
    "listusers": _users,
    "getuserkeys": _user_keys,
    "registeruserkeys": _register_user_keys,
    "deployvirtualmachine": _deploy,
    "startvirtualmachine": _start,
    "stopvirtualmachine": _stop,
    "rebootvirtualmachine": _reboot,
    "changeserviceforvirtualmachine": _resize_vm,
    "destroyvirtualmachine": _destroy,
    "restorevirtualmachine": _restore,
    "reinstallvirtualmachine": _reinstall,
    "createvolume": _create_volume,
    "attachvolume": _attach_volume,
    "detachvolume": _detach_volume,
    "resizevolume": _resize_volume,
    "deletevolume": _delete_volume,
    "createsnapshot": _create_snapshot,
    "createnetwork": _create_network,
    "deletenetwork": _delete_network,
    "createfirewallrule": _create_firewall,
    "deletefirewallrule": _delete_firewall,
    "registersshkeypair": _register_key,
    "createconsoleendpoint": _console,
    "queryasyncjobresult": _job_result,
    "listusagerecords": _usage,
}


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, access_log=False)
