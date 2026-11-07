"""Typed action catalogue.

Every mutation the platform can perform has exactly one typed parameter model
with ``extra="forbid"``. The model, the approval policy, the cost effect, and
the required MCP scope are declared together so they cannot drift apart.

No action accepts a shell command, SQL statement, URL, or credential.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from conductor.errors import ValidationFailure
from conductor.models.enums import ActionType, ApprovalRequirement, GuestTaskType, Scope

CloudStackId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    ),
]


class ActionParams(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmptyParams(ActionParams):
    pass


class AccountContext(ActionParams):
    account_id: str | None = None
    project_id: CloudStackId | None = None


class QuoteParams(AccountContext):
    offering_id: CloudStackId
    network_ids: list[CloudStackId] = Field(default_factory=list, max_length=8)
    disk_size_gib: Decimal | None = Field(default=None, ge=0, le=65_536)


class ListResourcesParams(AccountContext):
    kind: Literal["instance", "volume", "network", "offering", "template", "all"] = "all"


class ResourceRef(ActionParams):
    instance_id: CloudStackId


class ConsoleParams(ResourceRef):
    pass


class CreateInstanceParams(AccountContext):
    name: str = Field(min_length=1, max_length=63, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    offering_id: CloudStackId
    template_id: CloudStackId
    zone_id: CloudStackId
    network_ids: list[CloudStackId] = Field(min_length=1, max_length=8)
    disk_size_gib: Decimal | None = Field(default=None, ge=0, le=65_536)
    disk_offering_id: CloudStackId | None = None
    ssh_key_name: str | None = Field(default=None, max_length=210)
    start: bool = True
    group: str | None = Field(default=None, max_length=100)

    @field_validator("network_ids")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("network_ids must be unique")
        return value


class StopInstanceParams(ResourceRef):
    force: bool = False


class RebootInstanceParams(ResourceRef):
    pass


class ResizeInstanceParams(ResourceRef):
    offering_id: CloudStackId


class DeleteInstanceParams(ResourceRef):
    expunge: bool = False
    # Volumes are retained by default in CloudStack. Deleting them is an
    # explicit, disclosed choice, never an implicit side effect.
    delete_volume_ids: list[CloudStackId] = Field(default_factory=list, max_length=32)

    @field_validator("delete_volume_ids")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("delete_volume_ids must be unique")
        return value


class RestoreInstanceParams(ResourceRef):
    template_id: CloudStackId | None = None


class ReinstallInstanceParams(ResourceRef):
    template_id: CloudStackId | None = None
    preserve_root_password: bool = False


class CreateVolumeParams(AccountContext):
    name: str = Field(min_length=1, max_length=100)
    zone_id: CloudStackId
    size_gib: Decimal = Field(gt=0, le=65_536)
    disk_offering_id: CloudStackId | None = None
    snapshot_id: CloudStackId | None = None


class VolumeRef(ActionParams):
    volume_id: CloudStackId


class AttachVolumeParams(ActionParams):
    volume_id: CloudStackId
    instance_id: CloudStackId


class ResizeVolumeParams(VolumeRef):
    size_gib: Decimal = Field(gt=0, le=65_536)


class DeleteVolumeParams(VolumeRef):
    pass


class SnapshotRef(ActionParams):
    snapshot_id: CloudStackId


class CreateSnapshotParams(VolumeRef):
    description: str | None = Field(default=None, max_length=200)


class RestoreSnapshotParams(SnapshotRef):
    volume_id: CloudStackId | None = None


class FirewallRuleSpec(ActionParams):
    protocol: Literal["tcp", "udp", "icmp"] = "tcp"
    start_port: int = Field(default=0, ge=0, le=65_535)
    end_port: int = Field(default=0, ge=0, le=65_535)
    source_cidrs: list[str] = Field(min_length=1, max_length=16)
    action: Literal["allow", "deny"] = "allow"
    description: str | None = Field(default=None, max_length=200)

    @field_validator("source_cidrs")
    @classmethod
    def _cidrs(cls, values: list[str]) -> list[str]:
        import ipaddress

        result = []
        for value in values:
            try:
                result.append(str(ipaddress.ip_network(value, strict=False)))
            except ValueError as exc:
                raise ValueError("source_cidrs must contain valid CIDR ranges") from exc
        return result

    @field_validator("end_port")
    @classmethod
    def _ports(cls, value: int, info: object) -> int:
        start = getattr(info, "data", {}).get("start_port", 0)
        if value < start:
            raise ValueError("end_port must be greater than or equal to start_port")
        return value


class UpdateFirewallParams(ActionParams):
    network_id: CloudStackId
    add: list[FirewallRuleSpec] = Field(default_factory=list, max_length=32)
    remove_rule_ids: list[CloudStackId] = Field(default_factory=list, max_length=32)

    @field_validator("add")
    @classmethod
    def _rules_present(cls, value: list[FirewallRuleSpec], info: object) -> list[FirewallRuleSpec]:
        if not value and not getattr(info, "data", {}).get("remove_rule_ids"):
            raise ValueError("at least one rule must be added or removed")
        return value


class CreateNetworkParams(AccountContext):
    name: str = Field(min_length=1, max_length=100)
    display_text: str = Field(default="Isolated network", max_length=200)
    zone_id: CloudStackId
    network_offering_id: CloudStackId
    cidr: str = Field(default="192.0.2.0/24", max_length=43)
    gateway: str | None = Field(default=None, max_length=43)

    @field_validator("cidr")
    @classmethod
    def _private_cidr(cls, value: str) -> str:
        import ipaddress

        network = ipaddress.ip_network(value, strict=False)
        documentation = {
            ipaddress.ip_network("192.0.2.0/24"),
            ipaddress.ip_network("198.51.100.0/24"),
            ipaddress.ip_network("203.0.113.0/24"),
        }
        # Public examples use RFC 5737 documentation ranges; real deployments
        # use private address space. Other public ranges are refused.
        if not network.is_private and network not in documentation:
            raise ValueError("guest networks must use a private or documentation CIDR range")
        return str(network)


class NetworkRef(ActionParams):
    network_id: CloudStackId


class RegisterSshKeyParams(ActionParams):
    name: str = Field(min_length=1, max_length=210, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._@-]*$")
    public_key: str = Field(min_length=40, max_length=8192)

    @field_validator("public_key")
    @classmethod
    def _public_key(cls, value: str) -> str:
        if "PRIVATE KEY" in value:
            raise ValueError("a private key must never be uploaded")
        parts = value.strip().split()
        if len(parts) < 2 or parts[0] not in {
            "ssh-ed25519",
            "ssh-rsa",
            "ecdsa-sha2-nistp256",
            "ecdsa-sha2-nistp384",
            "ecdsa-sha2-nistp521",
        }:
            raise ValueError("unsupported SSH public key type")
        try:
            base64.b64decode(parts[1], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("the SSH public key is not valid base64") from exc
        if len(value) > 8192 or "\n" in value.strip():
            raise ValueError("unexpected SSH public key content")
        return value.strip()


class GuestTaskParams(ResourceRef):
    task: GuestTaskType
    parameters: dict[str, str] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def _safe_parameters(cls, value: dict[str, str]) -> dict[str, str]:
        allowed_keys = {
            "mountpoint",
            "device",
            "filesystem",
            "dry_run",
        }
        extra = set(value) - allowed_keys
        if extra:
            raise ValueError("unsupported guest task parameters: " + ", ".join(sorted(extra)))
        for key, item in value.items():
            if len(item) > 400:
                raise ValueError(f"guest task parameter {key} is too long")
            if "\n" in item or "\x00" in item or ";" in item or "&&" in item:
                raise ValueError("guest task parameters must be plain values")
            if key in {"mountpoint", "device"} and not re.fullmatch(r"[a-zA-Z0-9/_.:@+-]+", item):
                raise ValueError(f"guest task parameter {key} contains unsupported characters")
        return value


class DocsSearchParams(ActionParams):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=20)


@dataclass(frozen=True)
class ActionSpec:
    action: ActionType
    params_model: type[BaseModel]
    mutating: bool
    approval: ApprovalRequirement
    category: str
    cost_impact: Literal["none", "compute", "storage", "both"]
    required_scope: Scope
    title_zh: str
    title_en: str
    downtime_seconds: int | None = None
    destructive: bool = False
    explicit_when: tuple[str, ...] = field(default_factory=tuple)


_SPECS: tuple[ActionSpec, ...] = (
    ActionSpec(
        ActionType.QUOTE,
        QuoteParams,
        mutating=False,
        approval=ApprovalRequirement.NONE,
        category="read",
        cost_impact="none",
        required_scope=Scope.PRICING_READ,
        title_zh="查询费用估算",
        title_en="Quote a price",
    ),
    ActionSpec(
        ActionType.LIST_RESOURCES,
        ListResourcesParams,
        mutating=False,
        approval=ApprovalRequirement.NONE,
        category="read",
        cost_impact="none",
        required_scope=Scope.CLOUD_READ,
        title_zh="查看云资源",
        title_en="List cloud resources",
    ),
    ActionSpec(
        ActionType.OPEN_CONSOLE,
        ConsoleParams,
        mutating=False,
        approval=ApprovalRequirement.NONE,
        category="console",
        cost_impact="none",
        required_scope=Scope.CONSOLE_OPEN,
        title_zh="打开控制台",
        title_en="Open console",
    ),
    ActionSpec(
        ActionType.DOCS_SEARCH,
        DocsSearchParams,
        mutating=False,
        approval=ApprovalRequirement.NONE,
        category="read",
        cost_impact="none",
        required_scope=Scope.DOCS_READ,
        title_zh="检索文档",
        title_en="Search documentation",
    ),
    ActionSpec(
        ActionType.CREATE_INSTANCE,
        CreateInstanceParams,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="create",
        cost_impact="both",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="创建云主机",
        title_en="Create instance",
    ),
    ActionSpec(
        ActionType.START_INSTANCE,
        ResourceRef,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="power",
        cost_impact="compute",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="启动云主机",
        title_en="Start instance",
    ),
    ActionSpec(
        ActionType.STOP_INSTANCE,
        StopInstanceParams,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="power",
        cost_impact="compute",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="停止云主机",
        title_en="Stop instance",
        downtime_seconds=0,
    ),
    ActionSpec(
        ActionType.REBOOT_INSTANCE,
        RebootInstanceParams,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="power",
        cost_impact="none",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="重启云主机",
        title_en="Reboot instance",
        downtime_seconds=120,
    ),
    ActionSpec(
        ActionType.RESIZE_INSTANCE,
        ResizeInstanceParams,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="resize",
        cost_impact="compute",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="调整云主机规格",
        title_en="Resize instance",
        downtime_seconds=300,
    ),
    ActionSpec(
        ActionType.DELETE_INSTANCE,
        DeleteInstanceParams,
        mutating=True,
        approval=ApprovalRequirement.EXPLICIT,
        category="delete",
        cost_impact="both",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="删除云主机",
        title_en="Delete instance",
        destructive=True,
    ),
    ActionSpec(
        ActionType.RESTORE_INSTANCE,
        RestoreInstanceParams,
        mutating=True,
        approval=ApprovalRequirement.EXPLICIT,
        category="restore",
        cost_impact="compute",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="恢复云主机",
        title_en="Restore instance",
        destructive=True,
    ),
    ActionSpec(
        ActionType.REINSTALL_INSTANCE,
        ReinstallInstanceParams,
        mutating=True,
        approval=ApprovalRequirement.EXPLICIT,
        category="restore",
        cost_impact="compute",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="重装云主机",
        title_en="Reinstall instance",
        destructive=True,
    ),
    ActionSpec(
        ActionType.CREATE_VOLUME,
        CreateVolumeParams,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="storage",
        cost_impact="storage",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="创建数据盘",
        title_en="Create volume",
    ),
    ActionSpec(
        ActionType.ATTACH_VOLUME,
        AttachVolumeParams,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="storage",
        cost_impact="none",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="挂载数据盘",
        title_en="Attach volume",
    ),
    ActionSpec(
        ActionType.DETACH_VOLUME,
        VolumeRef,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="storage",
        cost_impact="none",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="卸载数据盘",
        title_en="Detach volume",
        downtime_seconds=60,
    ),
    ActionSpec(
        ActionType.RESIZE_VOLUME,
        ResizeVolumeParams,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="storage",
        cost_impact="storage",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="扩容数据盘",
        title_en="Expand volume",
    ),
    ActionSpec(
        ActionType.DELETE_VOLUME,
        DeleteVolumeParams,
        mutating=True,
        approval=ApprovalRequirement.EXPLICIT,
        category="delete",
        cost_impact="storage",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="删除数据盘",
        title_en="Delete volume",
        destructive=True,
    ),
    ActionSpec(
        ActionType.CREATE_SNAPSHOT,
        CreateSnapshotParams,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="snapshot",
        cost_impact="storage",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="创建快照",
        title_en="Create snapshot",
    ),
    ActionSpec(
        ActionType.RESTORE_SNAPSHOT,
        RestoreSnapshotParams,
        mutating=True,
        approval=ApprovalRequirement.EXPLICIT,
        category="restore",
        cost_impact="storage",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="恢复快照",
        title_en="Restore snapshot",
        destructive=True,
    ),
    ActionSpec(
        ActionType.CREATE_NETWORK,
        CreateNetworkParams,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="network",
        cost_impact="none",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="创建隔离网络",
        title_en="Create network",
    ),
    ActionSpec(
        ActionType.DELETE_NETWORK,
        NetworkRef,
        mutating=True,
        approval=ApprovalRequirement.EXPLICIT,
        category="delete",
        cost_impact="none",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="删除网络",
        title_en="Delete network",
        destructive=True,
    ),
    ActionSpec(
        ActionType.UPDATE_FIREWALL,
        UpdateFirewallParams,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="network",
        cost_impact="none",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="修改防火墙规则",
        title_en="Update firewall rules",
    ),
    ActionSpec(
        ActionType.REGISTER_SSH_KEY,
        RegisterSshKeyParams,
        mutating=True,
        approval=ApprovalRequirement.REQUIRED,
        category="account",
        cost_impact="none",
        required_scope=Scope.ACTIONS_PROPOSE,
        title_zh="登记 SSH 公钥",
        title_en="Register SSH key",
    ),
    ActionSpec(
        ActionType.GUEST_TASK,
        GuestTaskParams,
        mutating=True,
        approval=ApprovalRequirement.EXPLICIT,
        category="guest",
        cost_impact="none",
        required_scope=Scope.GUEST_PROPOSE,
        title_zh="执行受控客户机任务",
        title_en="Run managed guest task",
        destructive=True,
    ),
)

SPECS: dict[ActionType, ActionSpec] = {spec.action: spec for spec in _SPECS}


def get_spec(action: ActionType | str) -> ActionSpec:
    try:
        return SPECS[ActionType(action)]
    except (ValueError, KeyError) as exc:
        raise ValidationFailure("unsupported action") from exc


def validate_params(action: ActionType | str, params: dict[str, object]) -> BaseModel:
    spec = get_spec(action)
    try:
        return spec.params_model.model_validate(params)
    except ValueError as exc:
        raise ValidationFailure(f"invalid parameters for {spec.action.value}") from exc


def approval_requirement(action: ActionType | str, params: BaseModel | dict[str, object]) -> ApprovalRequirement:
    spec = get_spec(action)
    data = params if isinstance(params, dict) else params.model_dump()
    if spec.approval is ApprovalRequirement.EXPLICIT:
        return ApprovalRequirement.EXPLICIT
    if spec.explicit_when and any(bool(data.get(name)) for name in spec.explicit_when):
        return ApprovalRequirement.EXPLICIT
    return spec.approval


def is_mutating(action: ActionType | str) -> bool:
    return get_spec(action).mutating


__all__ = [
    "SPECS",
    "ActionParams",
    "ActionSpec",
    "AttachVolumeParams",
    "ConsoleParams",
    "CreateInstanceParams",
    "CreateNetworkParams",
    "CreateSnapshotParams",
    "CreateVolumeParams",
    "DeleteInstanceParams",
    "DeleteVolumeParams",
    "DocsSearchParams",
    "EmptyParams",
    "FirewallRuleSpec",
    "GuestTaskParams",
    "ListResourcesParams",
    "NetworkRef",
    "QuoteParams",
    "RebootInstanceParams",
    "RegisterSshKeyParams",
    "ReinstallInstanceParams",
    "ResizeInstanceParams",
    "ResizeVolumeParams",
    "ResourceRef",
    "RestoreInstanceParams",
    "RestoreSnapshotParams",
    "SnapshotRef",
    "StopInstanceParams",
    "UpdateFirewallParams",
    "VolumeRef",
    "approval_requirement",
    "get_spec",
    "is_mutating",
    "validate_params",
]
