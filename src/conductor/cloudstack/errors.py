"""CloudStack adapter exceptions.

Upstream text is never placed in a public message. The execution service logs
a redacted detail privately and stores only a stable error code on operations.
"""

from __future__ import annotations

from conductor.errors import CloudStackError, ReconciliationRequired


class CloudStackAPIError(CloudStackError):
    """CloudStack returned a structured API error."""

    def __init__(self, error_code: str | int | None, *, detail: str | None = None):
        super().__init__(detail, error_code=str(error_code) if error_code is not None else None)
        self.api_error_code = error_code


class CloudStackJobFailed(CloudStackAPIError):
    """An asynchronous CloudStack job finished in the failed state."""

    def __init__(self, job_id: str, error_code: str | int | None, *, detail: str | None = None):
        super().__init__(error_code, detail=detail)
        self.job_id = job_id


class CloudStackJobTimeout(ReconciliationRequired):
    def __init__(self, job_id: str):
        super().__init__("the CloudStack job did not finish within the timeout")
        self.job_id = job_id


class CloudStackProtocolError(CloudStackError):
    """The response was not valid CloudStack JSON."""


class CloudStackNotFound(CloudStackAPIError):
    code = "not_found"
    status_code = 404
    public_message = "The cloud resource was not found."
