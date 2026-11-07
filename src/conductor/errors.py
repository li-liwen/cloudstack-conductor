"""Typed domain errors.

Errors are deliberately free of deployment identifiers. Anything printed by a
service is safe to return to a browser or MCP client, so upstream exception
text is never copied into a message; callers log the upstream detail privately.
"""

from __future__ import annotations


class ConductorError(Exception):
    """Base class for expected application failures."""

    code = "conductor_error"
    status_code = 400
    public_message = "The request could not be completed."

    def __init__(self, detail: str | None = None, *, public_message: str | None = None):
        self.detail = detail or self.public_message
        if public_message is not None:
            self.public_message = public_message
        super().__init__(self.detail)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.public_message}


class ConfigurationError(ConductorError):
    code = "configuration_error"
    status_code = 500
    public_message = "The service is not configured correctly."


class AuthenticationError(ConductorError):
    code = "authentication_required"
    status_code = 401
    public_message = "Authentication is required."


class AuthorizationError(ConductorError):
    code = "not_authorized"
    status_code = 403
    public_message = "You are not authorized to perform this action."


class NotFoundError(ConductorError):
    code = "not_found"
    status_code = 404
    public_message = "The requested resource was not found."


class ConflictError(ConductorError):
    code = "conflict"
    status_code = 409
    public_message = "The request conflicts with the current state."


class ValidationFailure(ConductorError):
    code = "invalid_request"
    status_code = 422
    public_message = "The request was not valid."


class ApprovalRequired(ConductorError):
    code = "approval_required"
    status_code = 409
    public_message = "This action requires explicit approval."


class ApprovalExpired(ConductorError):
    code = "approval_expired"
    status_code = 409
    public_message = "The approval has expired; prepare the action again."


class PlanChanged(ConductorError):
    code = "plan_changed"
    status_code = 409
    public_message = "The plan changed after approval and must be approved again."


class QuotaExceeded(ConductorError):
    code = "quota_exceeded"
    status_code = 409
    public_message = "The requested change would exceed an account quota."


class CapabilityUnsupported(ConductorError):
    code = "unsupported"
    status_code = 422
    public_message = "The cloud does not support the requested operation."


class CloudStackError(ConductorError):
    """Safe wrapper around a CloudStack failure."""

    code = "cloudstack_error"
    status_code = 502
    public_message = "The cloud did not accept the request."

    def __init__(self, detail: str | None = None, *, error_code: str | None = None):
        super().__init__(detail)
        self.error_code = error_code


class CloudStackUnavailable(CloudStackError):
    code = "cloudstack_unavailable"
    status_code = 503
    public_message = "The cloud is temporarily unavailable."


class ReconciliationRequired(ConductorError):
    code = "needs_reconciliation"
    status_code = 409
    public_message = (
        "The cloud outcome is uncertain. The operation is marked for reconciliation "
        "and will not be retried automatically."
    )


class RateLimitExceeded(ConductorError):
    code = "rate_limited"
    status_code = 429
    public_message = "Too many requests; retry later."
