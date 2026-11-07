"""Identity-bridge failures.

Every public message is safe to return to a browser. Upstream response text,
passwords, one-time codes, tokens, and session keys are never placed in these
errors; the caller may log a stable error code privately.
"""

from __future__ import annotations

from conductor.errors import ConductorError


class IdentityBridgeError(ConductorError):
    code = "identity_bridge_error"
    status_code = 400
    public_message = "The identity migration request could not be completed."


class IdentityBridgeDisabled(IdentityBridgeError):
    code = "identity_bridge_disabled"
    status_code = 503
    public_message = "The identity bridge is not enabled on this deployment."


class IdentityNotProvisioned(IdentityBridgeError):
    code = "identity_not_provisioned"
    status_code = 403
    public_message = "This identity is not provisioned; contact an administrator."


class IdentityUsernameMismatch(IdentityBridgeError):
    code = "identity_username_mismatch"
    status_code = 403
    public_message = "The supplied username does not match the provisioned identity."


class IdentityMappingMismatch(IdentityBridgeError):
    code = "identity_mapping_mismatch"
    status_code = 403
    public_message = "The legacy login returned an identity that does not match the provisioned mapping."


class LegacyCredentialsInvalid(IdentityBridgeError):
    code = "legacy_credentials_invalid"
    status_code = 401
    public_message = "The legacy credentials could not be verified."


class LegacySecondFactorRequired(IdentityBridgeError):
    code = "legacy_second_factor_required"
    status_code = 401
    public_message = "A legacy second-factor code is required."


class LegacySecondFactorInvalid(IdentityBridgeError):
    code = "legacy_second_factor_invalid"
    status_code = 401
    public_message = "The legacy second-factor code could not be verified."


class MigrationStageConflict(IdentityBridgeError):
    code = "migration_stage_conflict"
    status_code = 409
    public_message = "The migration is not at the stage required for this step."


class MfaEnrollmentRequired(IdentityBridgeError):
    code = "mfa_enrollment_required"
    status_code = 409
    public_message = "Multi-factor authentication is required; enroll an authenticator before continuing."


class IdentityProviderError(IdentityBridgeError):
    code = "identity_provider_error"
    status_code = 502
    public_message = "The identity provider could not complete the request."


class IdentityProviderCapabilityError(IdentityBridgeError):
    code = "identity_provider_capability_missing"
    status_code = 422
    public_message = "The identity provider does not expose a capability required for this migration."


class SamlAuthorizationFailed(IdentityBridgeError):
    code = "saml_authorization_failed"
    status_code = 502
    public_message = "The cloud did not authorize SAML single sign-on for this user."


class LegacySessionUnavailable(IdentityBridgeError):
    code = "legacy_session_unavailable"
    status_code = 409
    public_message = "The temporary legacy login session is no longer available; restart the migration step."


class TlsRequired(IdentityBridgeError):
    code = "tls_required"
    status_code = 400
    public_message = "Credential migration is only accepted over TLS."
