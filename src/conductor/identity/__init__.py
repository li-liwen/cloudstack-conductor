"""SSO / identity-migration subsystem.

The package exposes the migration service, the optional FastAPI router, and the
configuration and adapter entry points. It does not modify the portal's service
bundle or mount itself; see :mod:`conductor.identity.routes` for the mounting
contract.
"""

from __future__ import annotations

from conductor.identity.config import DomainProviderMap, DomainSamlProvider, IdentityBridgeSettings
from conductor.identity.errors import (
    IdentityBridgeError,
    IdentityMappingMismatch,
    IdentityNotProvisioned,
    IdentityProviderCapabilityError,
    IdentityProviderError,
    LegacyCredentialsInvalid,
    LegacySecondFactorInvalid,
    LegacySecondFactorRequired,
    MfaEnrollmentRequired,
    MigrationStageConflict,
    SamlAuthorizationFailed,
    TlsRequired,
)
from conductor.identity.models import MigrationStatus
from conductor.identity.routes import build_router
from conductor.identity.service import IdentityMigrationService
from conductor.identity.sessions import TemporarySessionRegistry
from conductor.identity.wiring import build_identity_service

__all__ = [
    "DomainProviderMap",
    "DomainSamlProvider",
    "IdentityBridgeError",
    "IdentityBridgeSettings",
    "IdentityMappingMismatch",
    "IdentityMigrationService",
    "IdentityNotProvisioned",
    "IdentityProviderCapabilityError",
    "IdentityProviderError",
    "LegacyCredentialsInvalid",
    "LegacySecondFactorInvalid",
    "LegacySecondFactorRequired",
    "MfaEnrollmentRequired",
    "MigrationStageConflict",
    "MigrationStatus",
    "SamlAuthorizationFailed",
    "TemporarySessionRegistry",
    "TlsRequired",
    "build_identity_service",
    "build_router",
]
