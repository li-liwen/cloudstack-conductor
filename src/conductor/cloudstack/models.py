"""Typed CloudStack resource models.

Only fields the application actually uses are modelled. Everything is optional
except identifiers and states, because CloudStack's payload varies by version
and enabled features. Validation is deliberately lenient on unknown fields.
"""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CloudStackModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True, str_strip_whitespace=True)


def _parse_cs_datetime(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip().replace("'T'", "T").replace("Z", "+00:00")
    # CloudStack occasionally returns "2026-11-01T00:00:00+0000" (no colon).
    if len(text) >= 5 and text[-5] in "+-" and text[-4:].isdigit():
        text = text[:-2] + ":" + text[-2:]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Nic(CloudStackModel):
    id: str | None = None
    networkid: str | None = None
    netmask: str | None = None
    gateway: str | None = None
    ipaddress: str | None = None
    macaddress: str | None = None
    traffictype: str | None = None
    broadcasturi: str | None = None


class VirtualMachine(CloudStackModel):
    id: str
    name: str = ""
    displayname: str | None = None
    instance_name: str | None = Field(default=None, alias="instancename")
    state: str = "Unknown"
    zoneid: str | None = None
    zonename: str | None = None
    templateid: str | None = None
    templatename: str | None = None
    serviceofferingid: str | None = None
    serviceofferingname: str | None = None
    account: str | None = None
    accountid: str | None = None
    domain: str | None = None
    domainid: str | None = None
    project: str | None = None
    projectid: str | None = None
    hostid: str | None = None
    hostname: str | None = None
    hypervisor: str | None = None
    created: datetime | None = None
    tags: list[dict[str, str]] = Field(default_factory=list)
    nic: list[Nic] = Field(default_factory=list)
    ipaddress: str | None = None
    jobid: str | None = None
    jobstatus: int | None = None
    passwordenabled: bool = False

    @field_validator("created", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime | None:
        return _parse_cs_datetime(value)

    @property
    def ip_addresses(self) -> list[str]:
        values = [nic.ipaddress for nic in self.nic if nic.ipaddress]
        if self.ipaddress and self.ipaddress not in values:
            values.append(self.ipaddress)
        return values

    @property
    def running(self) -> bool:
        return self.state.lower() == "running"

    @property
    def stopped(self) -> bool:
        return self.state.lower() == "stopped"


class Volume(CloudStackModel):
    id: str
    name: str = ""
    type: str | None = None
    size: int | None = Field(default=None, description="bytes")
    state: str | None = None
    virtualmachineid: str | None = None
    vmname: str | None = None
    diskofferingid: str | None = None
    diskofferingname: str | None = None
    zoneid: str | None = None
    zonename: str | None = None
    account: str | None = None
    accountid: str | None = None
    domainid: str | None = None
    projectid: str | None = None
    created: datetime | None = None
    storage: str | None = None
    storageid: str | None = None
    jobid: str | None = None

    @field_validator("created", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime | None:
        return _parse_cs_datetime(value)

    @property
    def size_gib(self) -> Decimal:
        if not self.size:
            return Decimal(0)
        return (Decimal(self.size) / Decimal(1024**3)).quantize(Decimal("0.01"))

    @property
    def attached(self) -> bool:
        return bool(self.virtualmachineid)


class ServiceOffering(CloudStackModel):
    id: str
    name: str = ""
    displaytext: str | None = None
    cpunumber: int = 0
    cpuspeed: int = 0
    memory: int = Field(default=0, description="MiB")
    gpunumber: int = 0
    gputype: str | None = None
    offerha: bool = False
    iscustomized: bool = False
    defaultuse: bool = False
    zoneid: str | None = None
    created: datetime | None = None

    @field_validator("created", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime | None:
        return _parse_cs_datetime(value)

    @property
    def memory_gib(self) -> Decimal:
        return (Decimal(self.memory) / Decimal(1024)).quantize(Decimal("0.01"))


class DiskOffering(CloudStackModel):
    id: str
    name: str = ""
    displaytext: str | None = None
    disksize: int = Field(default=0, description="GiB when set")
    iscustomized: bool = False
    storagetype: str | None = None
    zoneid: str | None = None


class Network(CloudStackModel):
    id: str
    name: str = ""
    displaytext: str | None = None
    type: str | None = None
    state: str | None = None
    vlan: str | None = None
    cidr: str | None = None
    gateway: str | None = None
    netmask: str | None = None
    zoneid: str | None = None
    zonename: str | None = None
    account: str | None = None
    accountid: str | None = None
    domainid: str | None = None
    projectid: str | None = None
    broadcasturi: str | None = None
    traffictype: str | None = None
    isdefault: bool = False
    networkofferingid: str | None = None
    networkofferingname: str | None = None
    aclid: str | None = None
    jobid: str | None = None

    @field_validator("cidr")
    @classmethod
    def _cidr(cls, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return str(ipaddress.ip_network(value, strict=False))
        except ValueError:
            return None


class Template(CloudStackModel):
    id: str
    name: str = ""
    displaytext: str | None = None
    ostypeid: str | None = None
    ostypename: str | None = None
    size: int | None = None
    format: str | None = None
    hypervisor: str | None = None
    isready: bool = False
    ispublic: bool = False
    account: str | None = None
    domainid: str | None = None
    zoneid: str | None = None
    templatetype: str | None = None
    created: datetime | None = None

    @field_validator("created", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime | None:
        return _parse_cs_datetime(value)


class Zone(CloudStackModel):
    id: str
    name: str = ""
    type: str | None = None
    networktype: str | None = None
    state: str | None = None
    allocationstate: str | None = None


class Account(CloudStackModel):
    id: str
    name: str = ""
    accounttype: int | None = Field(default=None, description="0 user, 1 admin, 2 domain-admin")
    domain: str | None = None
    domainid: str | None = None
    state: str | None = None
    roleid: str | None = None
    rolename: str | None = None


class User(CloudStackModel):
    id: str
    username: str = ""
    firstname: str | None = None
    lastname: str | None = None
    email: str | None = None
    account: str | None = None
    accountid: str | None = None
    accounttype: int | None = None
    domain: str | None = None
    domainid: str | None = None
    state: str | None = None
    roleid: str | None = None
    rolename: str | None = None


class Snapshot(CloudStackModel):
    id: str
    name: str | None = None
    volumeid: str | None = None
    volumename: str | None = None
    state: str | None = None
    snapshottype: str | None = None
    created: datetime | None = None
    accountid: str | None = None
    domainid: str | None = None
    zoneid: str | None = None

    @field_validator("created", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime | None:
        return _parse_cs_datetime(value)


class Capabilities(CloudStackModel):
    cloudstackversion: str | None = None
    securitygroupsenabled: bool = False
    dynamicrolesenabled: bool = False
    allowusercreateprojects: bool = False
    kvmsnapshotenabled: bool = False
    userpublictemplateenabled: bool = False
    projectinviterequired: bool = False
    customdiskofferingenabled: bool = False
    regionsecondaryenabled: bool = False


class AsyncJob(CloudStackModel):
    jobid: str | None = None
    jobstatus: int | None = None
    jobresult: dict[str, object] = Field(default_factory=dict)
    errorcode: int | None = None
    errortext: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.jobstatus == 1

    @property
    def failed(self) -> bool:
        return self.jobstatus == 2


class ConsoleEndpoint(CloudStackModel):
    url: str
    host: str | None = None
    port: int | None = None

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        if not value.startswith(("https://", "wss://", "http://", "ws://")):
            raise ValueError("console endpoint is not an HTTP(S)/WS(S) URL")
        return value


class Host(CloudStackModel):
    id: str
    name: str | None = None
    state: str | None = None
    resourcestate: str | None = None
    type: str | None = None
    zoneid: str | None = None
    zonename: str | None = None
    hypervisor: str | None = None


class Router(CloudStackModel):
    id: str
    name: str | None = None
    state: str | None = None
    role: str | None = None
    publicip: str | None = None
    hostid: str | None = None
    podid: str | None = None
    zoneid: str | None = None
    accountid: str | None = None
    domainid: str | None = None
    projectid: str | None = None


class RawUsageRecord(CloudStackModel):
    usageid: str
    account: str | None = None
    accountid: str | None = None
    domainid: str | None = None
    zoneid: str | None = None
    resourcetype: str | None = None
    resourceid: str | None = None
    resourcename: str | None = None
    usagetype: int | None = None
    description: str | None = None
    usage: Decimal | None = None
    rawusage: Decimal | None = None
    virtualmachineid: str | None = None
    startdate: datetime | None = None
    enddate: datetime | None = None
    offeringid: str | None = None

    @field_validator("startdate", "enddate", mode="before")
    @classmethod
    def _dates(cls, value: object) -> datetime | None:
        return _parse_cs_datetime(value)

    @field_validator("usage", "rawusage", mode="before")
    @classmethod
    def _decimal(cls, value: object) -> Decimal | None:
        if value is None or value == "":
            return None
        return Decimal(str(value))
