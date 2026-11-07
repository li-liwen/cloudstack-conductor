import { ApiError } from "../api/client";
import type { MessageKey } from "../i18n/messages";
import type { TFunction } from "../i18n/I18nProvider";

/**
 * Map known API error codes to translated, safe messages.
 *
 * Unknown codes fall back to the API's public `message` field (the backend
 * never puts stack traces there), and unreadable payloads become a generic
 * message. Raw response bodies are never rendered.
 */
const CODE_KEYS: Record<string, MessageKey> = {
  authentication_required: "error.unauthorized",
  not_authorized: "error.forbidden",
  not_found: "error.notFound",
  conflict: "error.conflict",
  invalid_request: "error.invalid",
  validation_error: "error.invalid",
  approval_required: "error.approvalRequired",
  approval_expired: "error.approvalExpired",
  plan_changed: "error.planChanged",
  quota_exceeded: "error.quota",
  unsupported: "error.unsupported",
  cloudstack_error: "error.cloud",
  cloudstack_unavailable: "error.cloud",
  needs_reconciliation: "error.reconciliation",
  execution_unavailable: "error.execution",
  rate_limited: "error.rateLimited",
  configuration_error: "error.generic",
  internal_error: "error.generic",
  network_error: "error.network",
  demo_login_required: "error.forbidden",
};

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function userErrorMessage(error: unknown, t: TFunction): string {
  if (isAbortError(error)) return "";
  if (error instanceof ApiError) {
    if (error.status === 401) return t("error.sessionExpired");
    if (error.status === 403 && error.code === "not_authorized") return t("error.forbidden");
    const key = CODE_KEYS[error.code];
    if (key === "error.invalid" && error.fields.length > 0) {
      return t("error.invalidFields", { fields: error.fields.join(", ") });
    }
    if (key) return t(key);
    if (error.message) return error.message;
    return t("error.generic");
  }
  if (typeof error === "object" && error !== null && "message" in error) {
    // Only reachable for local thrown errors; never API response text.
    return t("error.generic");
  }
  return t("error.generic");
}

/** A translation key safe to store in state and translate at render time. */
export function errorKey(error: unknown): MessageKey {
  if (error instanceof ApiError) {
    return CODE_KEYS[error.code] ?? (error.status === 401 ? "error.sessionExpired" : "error.generic");
  }
  return "error.generic";
}
