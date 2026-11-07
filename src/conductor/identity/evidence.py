"""Non-secret transition evidence.

Only a SHA-256 digest of a small canonical record is stored. The record
contains stages and a stable subject identifier, never a password, OTP,
session key, bearer token, or upstream response body. The digest is useful for
correlating an audit row with a migration transition without retaining the
underlying identity document.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from conductor.models.enums import MappingStage


def transition_evidence(
    *,
    subject: str,
    action: str,
    from_stage: MappingStage,
    to_stage: MappingStage,
    provider_id: str | None = None,
    at: datetime | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    timestamp = (at or datetime.now(tz=UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "action": action,
        "subject": subject,
        "from": from_stage.value,
        "to": to_stage.value,
        "provider_id": provider_id,
        "at": timestamp,
    }
    if extra:
        # ``extra`` is caller-supplied and must already be non-secret. Reject
        # values that are not plain JSON scalars to keep the digest canonical.
        for key, value in extra.items():
            if isinstance(value, bool | int | str) or value is None:
                payload[str(key)] = value
            else:
                raise TypeError("evidence extras must be scalar values")
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
