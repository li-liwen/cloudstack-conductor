import type { MessageKey } from "../i18n/messages";
import type { TFunction } from "../i18n/I18nProvider";

const STATUS_KEYS: Record<string, MessageKey> = {
  draft: "status.draft",
  awaiting_approval: "status.awaiting_approval",
  approved: "status.approved",
  rejected: "status.rejected",
  expired: "status.expired",
  executed: "status.executed",
  cancelled: "status.cancelled",
  queued: "status.queued",
  running: "status.running",
  completed: "status.completed",
  failed: "status.failed",
  partially_succeeded: "status.partially_succeeded",
  cancel_requested: "status.cancel_requested",
  needs_reconciliation: "status.needs_reconciliation",
  pending: "status.pending",
  succeeded: "status.succeeded",
  skipped: "status.skipped",
  unknown: "status.unknown",
  active: "status.active",
  revoked: "status.revoked",
  unmigrated: "status.unmigrated",
  legacy_credentials_verified: "status.legacy_credentials_verified",
  idp_credentials_ready: "status.idp_credentials_ready",
  required_mfa_ready: "status.required_mfa_ready",
  cloudstack_saml_authorized: "status.cloudstack_saml_authorized",
  complete: "status.complete",
  priced: "status.priced",
  partial: "status.partial",
  unpriced: "status.unpriced",
  provisional: "status.provisional",
};

export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger";

const TONES: Record<string, StatusTone> = {
  awaiting_approval: "warning",
  queued: "warning",
  cancel_requested: "warning",
  partially_succeeded: "warning",
  partial: "warning",
  provisional: "warning",
  draft: "info",
  running: "info",
  pending: "info",
  approved: "success",
  executed: "success",
  completed: "success",
  succeeded: "success",
  active: "success",
  complete: "success",
  priced: "success",
  failed: "danger",
  rejected: "danger",
  revoked: "danger",
  expired: "danger",
  cancelled: "danger",
  needs_reconciliation: "danger",
  unpriced: "danger",
};

export function statusLabel(t: TFunction, state: string | null | undefined): string {
  if (!state) return t("app.unknown");
  const key = STATUS_KEYS[state];
  return key ? t(key) : state.replaceAll("_", " ");
}

export function statusTone(state: string | null | undefined): StatusTone {
  return (state && TONES[state]) || "neutral";
}

export function approvalLabel(t: TFunction, requirement: string | null | undefined): string {
  if (requirement === "none") return t("status.approval.none");
  if (requirement === "explicit") return t("status.approval.explicit");
  if (requirement === "required") return t("status.approval.required");
  return requirement ?? t("app.unknown");
}

/** Scope values from the MCP API mapped to translated labels. */
export function scopeLabel(t: TFunction, scope: string): string {
  const map: Record<string, MessageKey> = {
    "docs:read": "connections.scope.docsRead",
    "cloud:read": "connections.scope.cloudRead",
    "pricing:read": "connections.scope.pricingRead",
    "usage:read": "connections.scope.usageRead",
    "console:open": "connections.scope.consoleOpen",
    "actions:propose": "connections.scope.actionsPropose",
    "guest:propose": "connections.scope.guestPropose",
  };
  const key = map[scope];
  return key ? t(key) : scope;
}

export function roleLabel(t: TFunction, role: string): string {
  if (role === "operator") return t("account.role.operator");
  if (role === "domain_admin") return t("account.role.domain_admin");
  if (role === "user") return t("account.role.user");
  return role;
}
