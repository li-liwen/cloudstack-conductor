"""Enumerations shared by the API, execution service, worker, and MCP server."""

from __future__ import annotations

from enum import StrEnum


class ActionType(StrEnum):
    QUOTE = "quote"
    LIST_RESOURCES = "list_resources"
    OPEN_CONSOLE = "open_console"
    CREATE_INSTANCE = "create_instance"
    START_INSTANCE = "start_instance"
    STOP_INSTANCE = "stop_instance"
    REBOOT_INSTANCE = "reboot_instance"
    RESIZE_INSTANCE = "resize_instance"
    DELETE_INSTANCE = "delete_instance"
    RESTORE_INSTANCE = "restore_instance"
    REINSTALL_INSTANCE = "reinstall_instance"
    CREATE_VOLUME = "create_volume"
    ATTACH_VOLUME = "attach_volume"
    DETACH_VOLUME = "detach_volume"
    RESIZE_VOLUME = "resize_volume"
    DELETE_VOLUME = "delete_volume"
    CREATE_SNAPSHOT = "create_snapshot"
    RESTORE_SNAPSHOT = "restore_snapshot"
    CREATE_NETWORK = "create_network"
    DELETE_NETWORK = "delete_network"
    UPDATE_FIREWALL = "update_firewall"
    REGISTER_SSH_KEY = "register_ssh_key"
    GUEST_TASK = "guest_task"
    DOCS_SEARCH = "docs_search"


class ApprovalRequirement(StrEnum):
    NONE = "none"
    REQUIRED = "required"
    EXPLICIT = "explicit"


class OperationState(StrEnum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    NEEDS_RECONCILIATION = "needs_reconciliation"

    @property
    def terminal(self) -> bool:
        return self in {
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.PARTIALLY_SUCCEEDED,
            OperationState.CANCELLED,
        }


class StepState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PriceStatus(StrEnum):
    PRICED = "priced"
    PARTIAL = "partial"
    UNPRICED = "unpriced"


class ResourceKind(StrEnum):
    INSTANCE = "instance"
    VOLUME = "volume"
    NETWORK = "network"
    OFFERING = "offering"
    TEMPLATE = "template"
    SNAPSHOT = "snapshot"


class Scope(StrEnum):
    DOCS_READ = "docs:read"
    CLOUD_READ = "cloud:read"
    PRICING_READ = "pricing:read"
    USAGE_READ = "usage:read"
    CONSOLE_OPEN = "console:open"
    ACTIONS_PROPOSE = "actions:propose"
    GUEST_PROPOSE = "guest:propose"


DEFAULT_MCP_SCOPES: tuple[Scope, ...] = (Scope.DOCS_READ, Scope.CLOUD_READ)


class ConsoleLeaseState(StrEnum):
    PENDING = "pending"
    ISSUED = "issued"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class MCPConnectionState(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class MappingStage(StrEnum):
    UNMIGRATED = "unmigrated"
    LEGACY_CREDENTIALS_VERIFIED = "legacy_credentials_verified"
    IDP_CREDENTIALS_READY = "idp_credentials_ready"
    REQUIRED_MFA_READY = "required_mfa_ready"
    CLOUDSTACK_SAML_AUTHORIZED = "cloudstack_saml_authorized"
    COMPLETE = "complete"


class PrincipalRole(StrEnum):
    USER = "user"
    DOMAIN_ADMIN = "domain_admin"
    OPERATOR = "operator"


class UsageCompleteness(StrEnum):
    COMPLETE = "complete"
    PROVISIONAL = "provisional"
    PARTIAL = "partial"


class GuestTaskType(StrEnum):
    INSPECT_FILESYSTEM = "inspect_filesystem"
    EXPAND_FILESYSTEM = "expand_filesystem"
    INSTALL_NGINX = "install_nginx"
    INSTALL_DOCKER = "install_docker"
    PROVISION_BASIC = "provision_basic"
